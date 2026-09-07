"""Deterministic offline replay of Phase 3 main provider requests.

The request journal proves that a response was durably associated with a logical call key,
but its hash is only meaningful if the finalizer independently rebuilds the request. This
module runs the normal Phase 3 resolver and shared judgment executor against journaled
responses. It then joins every replayed call to exactly one settled ledger success and checks
both request layers:

* the pre-role-limit logical request fingerprint recorded by ``JournalingClient``; and
* the literal post-role-limit provider kwargs hash recorded in response metadata.

No provider or reviewer is callable here. Reviewer decisions must already exist in the frozen
decision store, and any attempt to consult a reviewer is an error. The result is evidence of
deterministic consistency under the pinned local code and trusted filesystem, not remote
provider attestation.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rejudge import api_client, phase3_runner
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_canary_execute import CellContext, execute_cell
from rejudge.phase2_canary_gate import CanaryCellHalted
from rejudge.phase2_dual_gate import DualGateDecisionStore
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    journal_key,
)


_ROLE_ALIASES = {"oracle_verification": "oracle"}
_ALLOWED_TERMINAL_REASONS = frozenset({"checker_malformed", "checker_unresolved"})
_LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD = (
    api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD)


class MainProviderProvenanceError(ValueError):
    """The durable main artifacts do not reconstruct one exact provider history."""


def build_provider_request_kwargs(
    *,
    model: str,
    messages: Any,
    temperature: Any,
    max_tokens: int,
    seed: int,
    streaming: bool,
    extra_request_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build provider kwargs through the unchanged production client method."""
    builder = object.__new__(api_client.RejudgeClient)
    builder.extra_request_fields = (
        {model: dict(extra_request_fields)} if extra_request_fields else {}
    )
    return builder._build_request_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        streaming=streaming,
    )


def compute_request_fields_sha256(request_kwargs: Mapping[str, Any]) -> str:
    """Hash request kwargs with the unchanged production canonical serializer."""
    return hashlib.sha256(
        api_client._canonical_json(dict(request_kwargs)).encode("utf-8")
    ).hexdigest()


def _identity_from_row(row: Mapping[str, Any], *, label: str) -> tuple[str, str, int, int]:
    try:
        cell_key = row["cell_key"]
        role = row["call_role"]
        slot = row["slot"]
        attempt = row["attempt"]
    except KeyError as exc:
        raise MainProviderProvenanceError(f"{label} omits call identity fields") from exc
    if not isinstance(cell_key, str) or not cell_key:
        raise MainProviderProvenanceError(f"{label} cell_key must be a non-empty string")
    if not isinstance(role, str) or not role:
        raise MainProviderProvenanceError(f"{label} call_role must be a non-empty string")
    if type(slot) is not int or slot < 0 or type(attempt) is not int or attempt < 0:
        raise MainProviderProvenanceError(
            f"{label} slot and attempt must be non-negative integers")
    return cell_key, role, slot, attempt


def _logical_identity(request_metadata: Mapping[str, Any] | None) -> tuple[str, str, int, int]:
    try:
        key = journal_key(request_metadata)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise MainProviderProvenanceError(
            "replayed provider call does not have a valid journal identity") from exc
    return key.cell_key, key.call_role, key.slot, key.attempt


def _transport_attempt(event: Mapping[str, Any], *, label: str) -> int:
    value = event.get("attempt")
    if type(value) is not int or value < 0:
        raise MainProviderProvenanceError(
            f"{label} transport attempt must be a non-negative integer")
    return value


def _attempt_id(event: Mapping[str, Any], *, label: str) -> str:
    value = event.get("attempt_id")
    if not isinstance(value, str) or not value:
        raise MainProviderProvenanceError(
            f"{label} attempt_id must be a non-empty string")
    return value


def _utc_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MainProviderProvenanceError(
            f"{label} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (ValueError, OverflowError) as exc:
        raise MainProviderProvenanceError(
            f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MainProviderProvenanceError(f"{label} must use UTC")
    return parsed


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _effective_max_tokens(
    role_limits: Mapping[str, Any], *, model: str, role: str, requested: int,
) -> int:
    model_limits = role_limits.get("model_role_limits")
    if not isinstance(model_limits, Mapping):
        raise MainProviderProvenanceError("role limits omit model_role_limits")
    limits = model_limits.get(model)
    if limits is None:
        return requested
    if not isinstance(limits, Mapping):
        raise MainProviderProvenanceError(f"role limits for {model!r} are not an object")
    resolved_role = _ROLE_ALIASES.get(role, role)
    entry = limits.get(resolved_role)
    if not isinstance(entry, Mapping):
        raise MainProviderProvenanceError(
            f"role limits omit ({model!r}, {resolved_role!r})")
    base = entry.get("base_role_max_tokens")
    effective = entry.get("effective_request_max_tokens")
    if type(base) is not int or base <= 0 or type(effective) is not int or effective <= 0:
        raise MainProviderProvenanceError(
            f"role limits for ({model!r}, {resolved_role!r}) are not positive integers")
    if requested not in {base, effective}:
        raise MainProviderProvenanceError(
            f"replayed max_tokens {requested!r} for ({model!r}, {resolved_role!r}) "
            f"is neither base {base} nor effective {effective}")
    return effective


def _request_settings(role_limits: Mapping[str, Any]) -> tuple[frozenset[str], Mapping[str, Any]]:
    settings = role_limits.get("request_settings")
    if not isinstance(settings, Mapping):
        raise MainProviderProvenanceError("role limits omit request_settings")
    streaming = settings.get("streaming_pinned_models")
    extras = settings.get("per_model_extra_fields")
    if not isinstance(streaming, (Mapping, list, tuple, set, frozenset)):
        raise MainProviderProvenanceError(
            "request_settings.streaming_pinned_models has an unsupported shape")
    if not isinstance(extras, Mapping):
        raise MainProviderProvenanceError(
            "request_settings.per_model_extra_fields must be an object")
    streaming_models = frozenset(streaming)
    if any(not isinstance(model, str) or not model for model in streaming_models):
        raise MainProviderProvenanceError("streaming model IDs must be non-empty strings")
    return streaming_models, extras


class _NoReviewer:
    def __call__(self, _query: str, _candidate_a: str, _candidate_b: str) -> str:
        raise MainProviderProvenanceError(
            "provider replay attempted an unrecorded reviewer consultation")


class _ReplayClient:
    dry_run = False

    def __init__(
        self,
        *,
        journal_rows: Sequence[Mapping[str, Any]],
        ledger_events: Sequence[Mapping[str, Any]],
        role_limits: Mapping[str, Any],
        authorization_approved_at: datetime,
        authorization_valid_until: datetime,
        finalization_recorded_at: datetime,
    ) -> None:
        self._journal: dict[tuple[str, str, int, int], Mapping[str, Any]] = {}
        for index, row in enumerate(journal_rows):
            identity = _identity_from_row(row, label=f"request journal row {index}")
            if identity in self._journal:
                raise MainProviderProvenanceError("request journal repeats a call identity")
            self._journal[identity] = row

        self._streaming_models, self._extra_request_fields = _request_settings(
            role_limits)

        self._lifecycles: dict[
            tuple[str, str, int, int], tuple[dict[str, Any], ...]
        ] = {}
        # Amendment 14 (2026-09-06): a logical call may carry earlier dispatch episodes that
        # ended in a durable ``unknown_charge`` (an unobserved transport failure) before its
        # final successful episode. Each failed episode is kept apart from the success
        # lifecycle so downstream request hashing keeps its exact single-success contract.
        self._failed_episodes: dict[
            tuple[str, str, int, int], list[tuple[dict[str, Any], ...]]
        ] = {}
        self.redispatch_counts: dict[tuple[str, str, int, int], int] = {}
        self.unknown_charge_episodes_by_model: dict[str, int] = {}
        reservations: dict[str, dict[str, Any]] = {}
        open_attempt_keys: set[tuple[tuple[str, str, int, int], int]] = set()
        completed_attempt_ids: set[str] = set()
        records: dict[tuple[str, str, int, int], dict[int, dict[str, Any]]] = {}
        logical_dispatch_authorized_at_by_identity: dict[
            tuple[str, str, int, int], str
        ] = {}
        dynamically_released_models: set[str] = set()
        pending_stream_retry: tuple[str, str, int, int] | None = None
        latest_ledger_event_at: datetime | None = None
        latest_logical_dispatch_at: datetime | None = None
        latest_provider_completion_at: datetime | None = None
        genesis_seen = False
        for index, event in enumerate(ledger_events):
            status = event.get("status")
            if status == "ledger_genesis":
                if genesis_seen or index != 0:
                    raise MainProviderProvenanceError(
                        "main ledger contains a repeated or displaced genesis event")
                genesis_seen = True
                continue
            if status not in {"reserved", "released_no_charge", "success", "unknown_charge"}:
                raise MainProviderProvenanceError(
                    f"main ledger event {index} has unsupported status {status!r}")
            event_at = _utc_datetime(
                event.get("ts"), label=f"main ledger event {index} ts")
            if latest_ledger_event_at is not None and event_at < latest_ledger_event_at:
                raise MainProviderProvenanceError(
                    "main ledger provider event timestamps are out of order")
            latest_ledger_event_at = event_at
            metadata = event.get("metadata")
            if not isinstance(metadata, Mapping):
                raise MainProviderProvenanceError(
                    f"main ledger event {index} has no metadata object")
            identity = _logical_identity(metadata)
            transport_attempt = _transport_attempt(
                event, label=f"main ledger event {index}")
            attempt_id = _attempt_id(event, label=f"main ledger event {index}")
            model = event.get("model")
            if not isinstance(model, str) or not model:
                raise MainProviderProvenanceError(
                    f"main ledger event {index} model must be a non-empty string")

            if status == "reserved":
                if reservations:
                    raise MainProviderProvenanceError(
                        "serial main ledger contains overlapping provider reservations")
                prior_episode = records.get(identity)
                if transport_attempt == 0 and prior_episode:
                    ordered_prior = tuple(
                        prior_episode[position] for position in sorted(prior_episode))
                    if ordered_prior[-1]["status"] != "unknown_charge":
                        raise MainProviderProvenanceError(
                            "main ledger redispatches a logical call whose prior episode "
                            f"did not end in an unknown charge: {identity!r}")
                    if pending_stream_retry == identity:
                        raise MainProviderProvenanceError(
                            "main ledger redispatches a logical call inside its streaming "
                            f"retry: {identity!r}")
                    self._failed_episodes.setdefault(identity, []).append(ordered_prior)
                    records[identity] = {}
                completed_for_identity = records.setdefault(identity, {})
                if transport_attempt != len(completed_for_identity):
                    raise MainProviderProvenanceError(
                        "serial main ledger transport attempts are out of order")
                if transport_attempt == 0:
                    authorized_at_raw = metadata.get(
                        _LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD)
                    authorized_at = _utc_datetime(
                        authorized_at_raw,
                        label=(
                            f"main ledger event {index} "
                            f"metadata.{_LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD}"),
                    )
                    if not (
                        authorization_approved_at
                        <= authorized_at
                        <= authorization_valid_until
                    ):
                        raise MainProviderProvenanceError(
                            "logical provider dispatch began outside the signed "
                            "authorization window")
                    if authorized_at > event_at:
                        raise MainProviderProvenanceError(
                            "logical provider dispatch authorization timestamp is after "
                            "its ledger reservation")
                    logical_dispatch_authorized_at_by_identity[identity] = str(
                        authorized_at_raw)
                    if (
                        latest_logical_dispatch_at is None
                        or authorized_at > latest_logical_dispatch_at
                    ):
                        latest_logical_dispatch_at = authorized_at
                elif pending_stream_retry != identity:
                    raise MainProviderProvenanceError(
                        "provider transport retry is not the same logical call's "
                        "immediate streaming retry")
                elif metadata.get(_LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD) != (
                    logical_dispatch_authorized_at_by_identity.get(identity)
                ):
                    raise MainProviderProvenanceError(
                        "provider streaming retry did not inherit its logical dispatch "
                        "authorization timestamp")
                if pending_stream_retry is not None and identity != pending_stream_retry:
                    raise MainProviderProvenanceError(
                        "serial main ledger interposes a call before its streaming retry")
                if attempt_id in reservations or attempt_id in completed_attempt_ids:
                    raise MainProviderProvenanceError(
                        "main ledger repeats a provider attempt_id")
                attempt_key = (identity, transport_attempt)
                if (
                    attempt_key in open_attempt_keys
                    or transport_attempt in records.setdefault(identity, {})
                ):
                    raise MainProviderProvenanceError(
                        f"main ledger repeats transport attempt {attempt_key!r}")
                if model in self._streaming_models or transport_attempt > 0:
                    streaming_candidates = frozenset({True})
                elif model in dynamically_released_models:
                    # The formal main runner is serial. Once one call releases its probe, the
                    # live client enables streaming before any later reservation can begin.
                    streaming_candidates = frozenset({True})
                else:
                    streaming_candidates = frozenset({False})
                reservations[attempt_id] = {
                    "identity": identity,
                    "transport_attempt": transport_attempt,
                    "event": event,
                    "event_at": event_at,
                    "streaming_candidates": streaming_candidates,
                }
                open_attempt_keys.add(attempt_key)
                continue

            reserved = reservations.pop(attempt_id, None)
            if reserved is None:
                raise MainProviderProvenanceError(
                    f"main ledger terminal event {index} has no open reservation")
            completed_attempt_ids.add(attempt_id)
            if (
                reserved["identity"] != identity
                or reserved["transport_attempt"] != transport_attempt
            ):
                raise MainProviderProvenanceError(
                    "main ledger reservation and terminal event identities differ")
            open_attempt_keys.discard((identity, transport_attempt))
            reservation_event = reserved["event"]
            reservation_at = reserved["event_at"]
            if event_at < reservation_at:
                raise MainProviderProvenanceError(
                    "main ledger terminal timestamp precedes its reservation")
            if event_at > finalization_recorded_at:
                raise MainProviderProvenanceError(
                    "main ledger terminal timestamp is after finalization")
            if (
                latest_provider_completion_at is None
                or event_at > latest_provider_completion_at
            ):
                latest_provider_completion_at = event_at
            for field in (
                "model",
                "kind",
                "seed",
                "attempt",
                "estimated_tokens",
                "reserved_prompt_tokens",
                "reserved_completion_tokens",
                "metadata",
            ):
                if reservation_event.get(field) != event.get(field):
                    raise MainProviderProvenanceError(
                        "main ledger reservation and terminal event differ on "
                        f"{field}")
            lifecycle = records.setdefault(identity, {})
            if transport_attempt in lifecycle:
                raise MainProviderProvenanceError(
                    "main ledger repeats one logical transport attempt")
            lifecycle[transport_attempt] = {
                "status": status,
                "reservation": reservation_event,
                "terminal": event,
                "streaming_candidates": reserved["streaming_candidates"],
            }
            if status == "released_no_charge":
                if pending_stream_retry is not None:
                    raise MainProviderProvenanceError(
                        "serial main ledger begins a second streaming retry")
                pending_stream_retry = identity
                dynamically_released_models.add(model)
            elif pending_stream_retry == identity:
                pending_stream_retry = None

        if reservations:
            raise MainProviderProvenanceError(
                "main ledger contains unmatched provider reservations")
        if pending_stream_retry is not None:
            raise MainProviderProvenanceError(
                "main ledger omits the immediate streaming retry")
        for identity, episodes in self._failed_episodes.items():
            for episode in episodes:
                episode_statuses = [str(item["status"]) for item in episode]
                episode_attempts = [
                    _transport_attempt(item["terminal"], label="failed provider attempt")
                    for item in episode]
                if not (
                    (episode_attempts == [0] and episode_statuses == ["unknown_charge"])
                    or (episode_attempts == [0, 1]
                        and episode_statuses == ["released_no_charge", "unknown_charge"])
                ):
                    raise MainProviderProvenanceError(
                        "main ledger has an unsupported failed provider episode for "
                        f"{identity!r}: attempts={episode_attempts!r}, "
                        f"statuses={episode_statuses!r}")
                for item in episode:
                    if item["terminal"].get("response_metadata") is not None:
                        raise MainProviderProvenanceError(
                            "unknown-charge attempt carries response metadata for "
                            f"{identity!r}")
                    if (item["terminal"].get("prompt_tokens") is not None
                            or item["terminal"].get("completion_tokens") is not None):
                        raise MainProviderProvenanceError(
                            f"unknown-charge attempt carries provider usage for {identity!r}")
                model = str(episode[-1]["terminal"].get("model"))
                self.unknown_charge_episodes_by_model[model] = (
                    self.unknown_charge_episodes_by_model.get(model, 0) + 1)
            self.redispatch_counts[identity] = len(episodes)
        for identity, by_attempt in records.items():
            ordered = tuple(by_attempt[index] for index in sorted(by_attempt))
            attempts = sorted(by_attempt)
            statuses = [str(item["status"]) for item in ordered]
            if attempts == [0] and statuses == ["success"]:
                pass
            elif attempts == [0, 1] and statuses == [
                "released_no_charge", "success",
            ]:
                if True not in ordered[1]["streaming_candidates"]:
                    raise MainProviderProvenanceError(
                        "streaming negotiation retry is not reconstructed as streaming")
            else:
                raise MainProviderProvenanceError(
                    f"main ledger has an unsupported provider lifecycle for {identity!r}: "
                    f"attempts={attempts!r}, statuses={statuses!r}")
            self._lifecycles[identity] = ordered

        self._role_limits = role_limits
        self._logical_dispatch_authorized_at_by_identity = (
            logical_dispatch_authorized_at_by_identity)
        self.latest_logical_dispatch_at_utc = (
            None
            if latest_logical_dispatch_at is None
            else _canonical_utc(latest_logical_dispatch_at)
        )
        self.latest_provider_completion_at_utc = (
            None
            if latest_provider_completion_at is None
            else _canonical_utc(latest_provider_completion_at)
        )
        self._consumed: set[tuple[str, str, int, int]] = set()
        self.logical_request_hashes: dict[tuple[str, str, int, int], str] = {}
        self.provider_request_hashes: dict[
            tuple[str, str, int, int, int], str
        ] = {}

    def complete(
        self,
        messages: Any,
        model: str,
        temperature: Any,
        seed: int,
        max_tokens: int,
        kind: str = "verdict",
        *,
        request_metadata: Mapping[str, Any] | None = None,
    ) -> str:
        identity = _logical_identity(request_metadata)
        if identity in self._consumed:
            raise MainProviderProvenanceError(
                f"normal execution replayed logical call {identity!r} more than once")
        journal_row = self._journal.get(identity)
        if journal_row is None:
            raise MainProviderProvenanceError(
                f"normal execution produced unjournaled logical call {identity!r}")

        logical_sha = request_fingerprint(
            messages=messages,
            model=model,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
        )
        if journal_row.get("request_sha256") != logical_sha:
            raise MainProviderProvenanceError(
                f"journaled logical request fingerprint differs for {identity!r}")

        lifecycle = self._lifecycles.get(identity)
        if lifecycle is None:
            raise MainProviderProvenanceError(
                f"journaled logical call {identity!r} has no complete ledger lifecycle")
        expected_metadata = dict(request_metadata or {})
        expected_metadata[JOURNAL_REQUEST_SHA256_FIELD] = logical_sha
        expected_metadata[_LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD] = (
            self._logical_dispatch_authorized_at_by_identity[identity])
        for item in lifecycle:
            for event in (item["reservation"], item["terminal"]):
                if event.get("metadata") != expected_metadata:
                    raise MainProviderProvenanceError(
                        "ledger metadata differs from the exact replayed metadata for "
                        f"{identity!r}")
                if event.get("model") != model or event.get("kind") != kind:
                    raise MainProviderProvenanceError(
                        f"ledger model or kind differs from the exact replay for {identity!r}")
                if event.get("seed") != seed:
                    raise MainProviderProvenanceError(
                        f"ledger seed differs from the exact replay for {identity!r}")
        expected_without_dispatch_stamp = {
            field: value for field, value in expected_metadata.items()
            if field != _LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD}
        for episode in self._failed_episodes.get(identity, ()):
            for item in episode:
                for event in (item["reservation"], item["terminal"]):
                    observed = event.get("metadata")
                    if not isinstance(observed, Mapping):
                        raise MainProviderProvenanceError(
                            f"failed provider attempt has no metadata for {identity!r}")
                    stripped = {
                        field: value for field, value in observed.items()
                        if field != _LOGICAL_DISPATCH_AUTHORIZED_AT_FIELD}
                    if stripped != expected_without_dispatch_stamp:
                        raise MainProviderProvenanceError(
                            "failed provider attempt metadata differs from the exact "
                            f"replayed request for {identity!r}")
                    if event.get("model") != model or event.get("kind") != kind:
                        raise MainProviderProvenanceError(
                            "failed provider attempt model or kind differs from the exact "
                            f"replay for {identity!r}")
                    if event.get("seed") != seed:
                        raise MainProviderProvenanceError(
                            "failed provider attempt seed differs from the exact replay "
                            f"for {identity!r}")

        effective_max_tokens = _effective_max_tokens(
            self._role_limits,
            model=model,
            role=identity[1],
            requested=max_tokens,
        )
        extra_fields = self._extra_request_fields.get(model)
        if extra_fields is not None and not isinstance(extra_fields, Mapping):
            raise MainProviderProvenanceError(
                f"per-model extra request fields for {model!r} are not an object")
        success_item = lifecycle[-1]
        success = success_item["terminal"]
        response_metadata = success.get("response_metadata")
        if not isinstance(response_metadata, Mapping):
            raise MainProviderProvenanceError(
                f"ledger success for {identity!r} has no response metadata")
        observed_provider_sha = response_metadata.get("request_fields_sha256")
        matching_success_hashes: list[str] = []
        for streaming in sorted(success_item["streaming_candidates"]):
            provider_kwargs = build_provider_request_kwargs(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                seed=seed,
                streaming=streaming,
                extra_request_fields=extra_fields,
            )
            candidate_sha = compute_request_fields_sha256(provider_kwargs)
            if observed_provider_sha == candidate_sha:
                matching_success_hashes.append(candidate_sha)
        if len(matching_success_hashes) != 1:
            raise MainProviderProvenanceError(
                f"post-role-limit provider request hash differs for {identity!r}")
        if response_metadata.get("returned_model_id") != model:
            raise MainProviderProvenanceError(
                f"returned model identity differs for {identity!r}")

        for item in lifecycle[:-1]:
            transport_attempt = _transport_attempt(
                item["terminal"], label="released provider attempt")
            if item["status"] != "released_no_charge" or False not in item[
                    "streaming_candidates"]:
                raise MainProviderProvenanceError(
                    f"released provider attempt is not a non-streaming probe for {identity!r}")
            released_kwargs = build_provider_request_kwargs(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                seed=seed,
                streaming=False,
                extra_request_fields=extra_fields,
            )
            self.provider_request_hashes[
                (*identity, transport_attempt)
            ] = compute_request_fields_sha256(released_kwargs)
        success_attempt = _transport_attempt(
            success, label="successful provider attempt")
        self.provider_request_hashes[
            (*identity, success_attempt)
        ] = matching_success_hashes[0]

        response = journal_row.get("response")
        if not isinstance(response, str):
            raise MainProviderProvenanceError(
                f"journaled response for {identity!r} is not a string")
        self._consumed.add(identity)
        self.logical_request_hashes[identity] = logical_sha
        return response

    def assert_complete(self) -> None:
        journal = set(self._journal)
        lifecycles = set(self._lifecycles)
        if self._consumed != journal or self._consumed != lifecycles:
            raise MainProviderProvenanceError(
                "normal execution replay did not consume the exact journal and ledger sets: "
                f"unreplayed_journal={sorted(journal - self._consumed)!r}, "
                f"unreplayed_ledger={sorted(lifecycles - self._consumed)!r}, "
                f"unbacked_replays={sorted(self._consumed - (journal & lifecycles))!r}")


def _result_payloads(
    result_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(result_rows):
        cell_key = row.get("cell_key")
        payload = row.get("result")
        if not isinstance(cell_key, str) or not cell_key:
            raise MainProviderProvenanceError(
                f"result row {index} has no non-empty cell_key")
        if not isinstance(payload, Mapping):
            raise MainProviderProvenanceError(f"result row {index} has no result object")
        if cell_key in result:
            raise MainProviderProvenanceError("result rows repeat a cell key")
        result[cell_key] = payload
    return result


def _compare_result(
    *, cell_key: str, reconstructed: Mapping[str, Any], recorded: Mapping[str, Any],
) -> None:
    created_at = recorded.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise MainProviderProvenanceError(
            f"recorded judgment result {cell_key} has no creation timestamp")
    normalized = dict(reconstructed)
    normalized["created_at"] = created_at
    if normalized != dict(recorded):
        raise MainProviderProvenanceError(
            f"recorded judgment result {cell_key} differs from normal execution replay")


def _request_hash_set_sha256(
    hashes: Mapping[tuple[Any, ...], str],
) -> str:
    requests = []
    for identity in sorted(hashes):
        if len(identity) not in {4, 5}:  # pragma: no cover - internal construction invariant
            raise AssertionError("request hash identity has an unsupported width")
        row = {
            "cell_key": identity[0],
            "call_role": identity[1],
            "slot": identity[2],
            "application_attempt": identity[3],
            "request_sha256": hashes[identity],
        }
        if len(identity) == 5:
            row["transport_attempt"] = identity[4]
        requests.append(row)
    return compute_request_fields_sha256({"requests": requests})


def verify_main_provider_replay(
    *,
    cells: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
    terminal_cell_keys: Iterable[str],
    context_ineligible_cell_keys: Iterable[str],
    protocol: Mapping[str, Any],
    prompt_bundle: Mapping[str, Any],
    role_limits: Mapping[str, Any],
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    finalization_recorded_at_utc: str,
    review_decisions_path: str | Path,
    journal_rows: Sequence[Mapping[str, Any]],
    ledger_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay every non-context main judgment and prove its two request hashes exactly."""
    approved_at = _utc_datetime(
        authorization_approved_at_utc,
        label="authorization_approved_at_utc",
    )
    valid_until = _utc_datetime(
        authorization_valid_until_utc,
        label="authorization_valid_until_utc",
    )
    finalized_at = _utc_datetime(
        finalization_recorded_at_utc,
        label="finalization_recorded_at_utc",
    )
    if approved_at >= valid_until:
        raise MainProviderProvenanceError(
            "signed authorization dispatch window is empty")
    if finalized_at < approved_at:
        raise MainProviderProvenanceError(
            "finalization timestamp precedes signed authorization approval")
    terminal = frozenset(terminal_cell_keys)
    context = frozenset(context_ineligible_cell_keys)
    if terminal & context:
        raise MainProviderProvenanceError(
            "terminal and context-ineligible provider replay sets overlap")
    if any(not isinstance(key, str) or not key for key in terminal | context):
        raise MainProviderProvenanceError("provider replay cell keys must be non-empty strings")

    payloads = _result_payloads(result_rows)
    resolved = phase3_runner.resolve_main_cells(
        cells,
        protocol=protocol,
        bundle=prompt_bundle,
    )
    planned_keys = {cell.cell_key for cell in resolved}
    transcript_keys = {cell.cell_key for cell in resolved if cell.is_transcript}
    judgment_keys = {cell.cell_key for cell in resolved if not cell.is_transcript}
    if terminal - judgment_keys or context - judgment_keys:
        raise MainProviderProvenanceError(
            "provider replay exclusions contain unplanned or transcript cell keys")
    unknown_results = set(payloads) - planned_keys
    if unknown_results:
        raise MainProviderProvenanceError(
            "provider replay result rows contain unplanned cell keys: "
            f"{sorted(unknown_results)[:5]!r}")
    observed_transcripts = set(payloads) & transcript_keys
    if observed_transcripts != transcript_keys:
        raise MainProviderProvenanceError(
            "provider replay does not contain the exact transcript result partition")
    expected_judgment_results = judgment_keys - context - terminal
    observed_judgment_results = set(payloads) & judgment_keys
    if observed_judgment_results != expected_judgment_results:
        raise MainProviderProvenanceError(
            "provider replay does not contain the exact completed judgment partition")
    transcript_results = {
        cell.cell_key: dict(payloads[cell.cell_key])
        for cell in resolved
        if cell.is_transcript
    }
    observed_judgments = observed_judgment_results
    active_keys = observed_judgments | terminal
    expected_active = judgment_keys - context
    if active_keys != expected_active:
        raise MainProviderProvenanceError(
            "provider replay judgment partition differs from the planned active set")

    replay_client = _ReplayClient(
        journal_rows=journal_rows,
        ledger_events=ledger_events,
        role_limits=role_limits,
        authorization_approved_at=approved_at,
        authorization_valid_until=valid_until,
        finalization_recorded_at=finalized_at,
    )
    decision_store = DualGateDecisionStore(review_decisions_path)
    context_object = CellContext(
        client=replay_client,
        protocol=dict(protocol),
        bundle=dict(prompt_bundle),
        decision_store=decision_store,
        reviewer=_NoReviewer(),
        anchor_judge_model="",
        results=transcript_results,
        pause_when_unlabeled=False,
        transcript_generation_forbidden=True,
        role_limits=dict(role_limits),
    )
    namespace = protocol.get("cell_key_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise MainProviderProvenanceError("protocol omits cell_key_namespace")

    replayed_results = 0
    replayed_terminals = 0
    for cell in resolved:
        if cell.is_transcript or cell.cell_key in context:
            continue
        try:
            reconstructed = execute_cell(
                cell,
                context_object,
                debater_model=cell.debater_model,
                namespace=namespace,
            )
        except CanaryCellHalted as exc:
            if cell.cell_key not in terminal or exc.reason not in _ALLOWED_TERMINAL_REASONS:
                raise MainProviderProvenanceError(
                    f"normal execution unexpectedly halted {cell.cell_key}: {exc.reason}") from exc
            replayed_terminals += 1
            continue
        except MainProviderProvenanceError:
            raise
        except Exception as exc:
            raise MainProviderProvenanceError(
                f"normal execution replay failed for {cell.cell_key}: "
                f"{type(exc).__name__}: {exc}") from exc

        if cell.cell_key in terminal:
            raise MainProviderProvenanceError(
                f"terminal cell {cell.cell_key} completed during normal execution replay")
        recorded = payloads.get(cell.cell_key)
        if recorded is None:
            raise MainProviderProvenanceError(
                f"normal execution completed unrecorded judgment {cell.cell_key}")
        _compare_result(
            cell_key=cell.cell_key,
            reconstructed=reconstructed,
            recorded=recorded,
        )
        context_object.results[cell.cell_key] = dict(recorded)
        replayed_results += 1

    replay_client.assert_complete()
    return {
        "status": "exact_normal_execution_replay",
        "replayed_judgment_count": replayed_results,
        "replayed_terminal_count": replayed_terminals,
        "logical_request_count": len(replay_client.logical_request_hashes),
        "provider_request_count": len(replay_client.provider_request_hashes),
        "redispatched_logical_call_count": sum(
            1 for count in replay_client.redispatch_counts.values() if count > 0),
        "unknown_charge_episode_count": sum(replay_client.redispatch_counts.values()),
        "unknown_charge_episodes_by_model": dict(sorted(
            replay_client.unknown_charge_episodes_by_model.items())),
        "logical_request_hashes_sha256": _request_hash_set_sha256(
            replay_client.logical_request_hashes),
        "provider_request_hashes_sha256": _request_hash_set_sha256(
            replay_client.provider_request_hashes),
        "authorization_dispatch_status": (
            "all_logical_dispatches_within_authorization"),
        "latest_logical_dispatch_at_utc": (
            replay_client.latest_logical_dispatch_at_utc),
        "latest_provider_completion_at_utc": (
            replay_client.latest_provider_completion_at_utc),
    }


__all__ = [
    "MainProviderProvenanceError",
    "verify_main_provider_replay",
]
