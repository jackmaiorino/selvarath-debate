"""Live driver for the authorized canary run: validation, wiring, passes.

Deliberately OUTSIDE the frozen canary code-provenance set: this module executes no science.
Every scientific behaviour it wires together (composition, gates, judge loop, checker,
caching) is hash-bound into the manifest via ``CANARY_CODE_PROVENANCE_FILES``, and the first
thing a live run does is revalidate that manifest against the artifacts and sources on disk,
so drift in any bound file refuses the run regardless of what this driver does.

Fail-closed properties, mirroring the preflight's ``run_live``:

- a separate authorization record is REQUIRED; there is no parameter to bypass it, and the
  record must cross-match the manifest on identity, stage, and both caps exactly;
- the client cap is the manifest's OWN stage cap, never the cumulative cap;
- the frozen reviewer prompt is re-hashed against both its own declared sha and the
  manifest's ``frozen_inputs.reviewer_prompt_sha256`` before the first reviewer call;
- reviewer transport failure surfaces as an exception, which the dual gate commits as
  ``reviewer_error`` -> non-ALLOW, per the frozen failure rule.

The reviewer is claude-fable-5 over the raw Anthropic HTTP API via the standard library:
the ``anthropic`` SDK is not a dependency and ``uv.lock``'s raw hash is manifest-bound, so
adding it is not an option. Each payload is reviewed in a fresh request carrying exactly the
frozen prompt and the payload, which satisfies the amendment's per-batch isolation floor
(no repository, tools, memory, or cross-query discussion) at per-query granularity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from rejudge import api_client
from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_canary_manifest import validate_canary_manifest
from rejudge.phase2_canary_runner import RunOutcome, run_canary
from rejudge.phase2_execution import canonical_sha256
from rejudge.phase2_preflight_runner import _lazy_together_sdk_client
from rejudge.phase2_role_limits import resolve_transport_ledger_max_retries

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
REVIEWER_MAX_TOKENS = 300
REVIEWER_TEMPERATURE = 0
REVIEWER_TIMEOUT_SECONDS = 120.0
REVIEWER_MAX_ATTEMPTS = 3
_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 529})

AUTHORIZATION_KEYS = frozenset({
    "approval_basis_sha256", "approval_basis_tracked_path", "approved_at_utc", "approver",
    "cumulative_cap_usd", "execution_identity_sha256", "recorded_at_utc", "stage",
    "stage_cap_usd",
})


class CanaryLiveError(ValueError):
    """Raised when the live driver refuses to proceed. Always fails closed."""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_canary_authorization(record: Mapping[str, Any], manifest: Mapping[str, Any],
                                  project_root: str | Path = ".") -> dict[str, Any]:
    """Cross-check the flat authorization record against the validated manifest."""
    if not isinstance(record, Mapping):
        raise CanaryLiveError("authorization record must be a mapping")
    keys = set(record)
    if keys != AUTHORIZATION_KEYS:
        raise CanaryLiveError(
            f"authorization fields drifted: unexpected {sorted(keys - AUTHORIZATION_KEYS)!r}, "
            f"missing {sorted(AUTHORIZATION_KEYS - keys)!r}")
    if record["stage"] != manifest["stage"]:
        raise CanaryLiveError(
            f"authorization stage {record['stage']!r} does not match manifest stage "
            f"{manifest['stage']!r}")
    if record["execution_identity_sha256"] != manifest["execution_identity_sha256"]:
        raise CanaryLiveError(
            "authorization names a different execution identity than the manifest")
    if record["stage_cap_usd"] != manifest["caps"]["stage_cap_usd"]:
        raise CanaryLiveError("authorization stage cap does not match the manifest cap")
    if record["cumulative_cap_usd"] != manifest["caps"]["cumulative_cap_usd"]:
        raise CanaryLiveError("authorization cumulative cap does not match the manifest cap")
    if not isinstance(record["approver"], str) or not record["approver"].strip():
        raise CanaryLiveError("authorization approver must be a non-empty string")
    basis_path = Path(project_root) / str(record["approval_basis_tracked_path"])
    try:
        basis = _load_json(basis_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryLiveError(f"approval basis is not resolvable: {basis_path}: {exc}") from exc
    observed = canonical_sha256(basis)
    if observed != record["approval_basis_sha256"]:
        raise CanaryLiveError(
            f"approval basis hash mismatch: declared {record['approval_basis_sha256']!r}, "
            f"observed {observed}")
    return dict(record)


def local_path(path_text: str) -> Path:
    """Map a manifest-recorded Windows drive path onto this host's filesystem.

    Manifests record archive paths in the owner's ``E:/...`` notation. Under WSL the same
    drive is mounted at ``/mnt/e``. The manifest string stays authoritative; only filesystem
    access is translated, and only on a POSIX host.
    """
    if os.name == "posix" and len(path_text) > 2 and path_text[1] == ":" and (
            path_text[2] in "/\\"):
        return Path("/mnt/" + path_text[0].lower() + "/" + path_text[3:].replace("\\", "/"))
    return Path(path_text)


def load_frozen_reviewer_prompt(manifest: Mapping[str, Any],
                                project_root: str | Path = ".") -> dict[str, str]:
    tracked = manifest["reviewer"]["prompt_tracked_path"]
    artifact = _load_json(Path(project_root) / tracked)
    prompt = artifact["prompt"]
    observed = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if observed != artifact["prompt_sha256"]:
        raise CanaryLiveError(f"reviewer prompt drifted from its own declared sha in {tracked}")
    if observed != manifest["frozen_inputs"]["reviewer_prompt_sha256"]:
        raise CanaryLiveError("reviewer prompt does not match the manifest binding")
    return {"prompt": prompt, "model": str(manifest["reviewer"]["model"])}


def render_reviewer_payload(raw_query: str, candidate_a: str, candidate_b: str) -> str:
    # The exact payload_format documented in the frozen reviewer prompt artifact.
    return f"QUERY: {raw_query}\nCANDIDATE A: {candidate_a}\nCANDIDATE B: {candidate_b}"


class ClaudeReviewer:
    """The metadata-blinded second gate: frozen prompt + payload, nothing else.

    Each call is a fresh, isolated request: no conversation state, no tools, no metadata.
    Transient provider failures are retried a bounded number of times; a final failure
    raises, and the dual gate commits it as ``reviewer_error`` -> non-ALLOW.
    """

    def __init__(self, *, prompt: str, model: str, api_key: str,
                 timeout: float = REVIEWER_TIMEOUT_SECONDS,
                 max_attempts: int = REVIEWER_MAX_ATTEMPTS,
                 transport=None, sleep=time.sleep) -> None:
        if not api_key:
            raise CanaryLiveError("reviewer requires a non-empty API key")
        self._prompt = prompt
        self._model = model
        self._api_key = api_key
        self._timeout = float(timeout)
        self._max_attempts = int(max_attempts)
        self._transport = transport if transport is not None else self._http_post
        self._sleep = sleep
        self.calls = 0

    def _http_post(self, body: dict) -> dict:
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL, method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", "x-api-key": self._api_key,
                     "anthropic-version": ANTHROPIC_VERSION})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def __call__(self, raw_query: str, candidate_a: str, candidate_b: str) -> str:
        body = {
            "model": self._model,
            "max_tokens": REVIEWER_MAX_TOKENS,
            "temperature": REVIEWER_TEMPERATURE,
            # cache_control is transport-level cost engineering: the model-visible input is
            # byte-identical with or without it.
            "system": [{"type": "text", "text": self._prompt,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": render_reviewer_payload(
                raw_query, candidate_a, candidate_b)}],
        }
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                self.calls += 1
                data = self._transport(body)
                return str(data["content"][0]["text"])
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in _RETRYABLE_HTTP_STATUSES:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self._max_attempts:
                self._sleep(2 * attempt)
        raise CanaryLiveError(
            f"reviewer unavailable after {self._max_attempts} attempts: {last_error}")


def _client_construction_inputs(role_limits: Mapping[str, Any],
                                snapshot: Mapping[str, Any]):
    """The same four-field extraction the preflight builder and runner perform."""
    request_settings = role_limits["request_settings"]
    model_context_limits = {
        model_id: int(entry["context_length_tokens"])
        for model_id, entry in role_limits["context_ceilings"].items()}
    streaming_pinned_models = frozenset(request_settings["streaming_pinned_models"])
    extra_request_fields = {
        model_id: dict(fields)
        for model_id, fields in request_settings["per_model_extra_fields"].items()}
    model_prices = {
        model_id: {"in": float(entry["input_usd_per_million_tokens"]),
                   "out": float(entry["output_usd_per_million_tokens"])}
        for model_id, entry in snapshot["models"].items()}
    return model_context_limits, streaming_pinned_models, extra_request_fields, model_prices


def build_live_client(manifest: Mapping[str, Any], *, project_root: str | Path,
                      usage_log_path: Path, error_log_path: Path,
                      call_cache_path: Path) -> CachingClient:
    root = Path(project_root)
    role_limits = _load_json(root / "rejudge" / "phase2_role_limits_v5_2026-07-19.json")
    snapshot = _load_json(
        root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json")
    limits, pinned, extra_fields, prices = _client_construction_inputs(role_limits, snapshot)
    transport = role_limits["request_settings"]["transport"]

    identity = api_client.prepare_usage_ledger(usage_log_path, allow_create=True)
    ledger_snapshot = api_client.load_chained_usage_ledger(
        usage_log_path, expected_identity=identity)

    client = api_client.RejudgeClient(
        approved_cap_usd=float(manifest["caps"]["stage_cap_usd"]),
        dry_run=False,
        error_log_path=str(error_log_path),
        max_retries=resolve_transport_ledger_max_retries(role_limits["request_settings"]),
        _sdk_client=_lazy_together_sdk_client(
            http_timeout=transport["http_timeout"],
            sdk_internal_max_retries=transport["sdk_internal_max_retries"]),
        model_prices=prices,
        strict_model_pricing=True,
        initial_spend_usd=float(ledger_snapshot.summary["actual_spend_usd"]),
        initial_uncertain_spend_usd=float(ledger_snapshot.summary["uncertain_spend_usd"]),
        usage_log_path=str(usage_log_path),
        _ledger_snapshot=ledger_snapshot,
        _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits=limits,
        strict_context_mode=True,
        streaming_pinned_models=pinned,
        extra_request_fields=extra_fields,
        halt_on_unknown_charge=True,
        http_timeout=dict(transport["http_timeout"]),
        sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        per_call_wall_clock_ceiling_seconds=float(
            transport["per_call_wall_clock_ceiling_seconds"]),
    )
    return CachingClient(client, CallCache(call_cache_path))


def run_live(manifest_path: str | Path, authorization_path: str | Path,
             project_root: str | Path = ".", *, limit: int | None = None,
             max_passes: int = 40, client=None, reviewer=None) -> RunOutcome:
    """Execute the authorized canary. The only entry point here that can spend money.

    ``client``/``reviewer`` are injectable for offline tests only; a live invocation leaves
    them None and gets the real capped client and the real Claude reviewer.
    """
    if authorization_path is None:
        raise CanaryLiveError("run_live requires an authorization_path; there is no bypass")
    manifest = _load_json(Path(manifest_path))
    validate_canary_manifest(manifest, project_root=project_root)
    authorization = _load_json(Path(authorization_path))
    validate_canary_authorization(authorization, manifest, project_root=project_root)

    if reviewer is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise CanaryLiveError(
                "ANTHROPIC_API_KEY is missing or blank; the reviewer gate cannot run")
        frozen = load_frozen_reviewer_prompt(manifest, project_root)
        reviewer = ClaudeReviewer(prompt=frozen["prompt"], model=frozen["model"],
                                  api_key=api_key)
    if client is None:
        if not os.environ.get("TOGETHER_API_KEY", "").strip():
            raise CanaryLiveError(
                "TOGETHER_API_KEY is missing or blank; refusing before any construction")
        archive_dir = local_path(manifest["ledger"]["archive_dir"])
        archive_dir.mkdir(parents=True, exist_ok=True)
        client = build_live_client(
            manifest, project_root=project_root,
            usage_log_path=local_path(manifest["ledger"]["usage_log_path"]),
            error_log_path=archive_dir / "canary_error_log.jsonl",
            call_cache_path=local_path(manifest["ledger"]["call_cache_path"]))

    results_path = local_path(manifest["ledger"]["results_path"])
    decisions_path = local_path(manifest["ledger"]["decisions_path"])

    outcome = RunOutcome()
    for pass_index in range(1, max_passes + 1):
        outcome = run_canary(
            results_path=results_path, decisions_path=decisions_path, client=client,
            reviewer=reviewer, anchor_judge_model=str(manifest["anchor"]["judge_model"]),
            pause_when_unlabeled=False, limit=limit)
        print(f"pass {pass_index}: completed={outcome.completed} "
              f"skipped={outcome.skipped} deferred={outcome.deferred} "
              f"paused={outcome.paused} halted={outcome.halted_reason or '-'}",
              flush=True)
        if outcome.halted_reason is not None or limit is not None:
            return outcome
        if outcome.deferred == 0:
            return outcome
        if outcome.completed == 0:
            raise CanaryLiveError(
                f"no progress: {outcome.deferred} cells still deferred after a full pass")
    raise CanaryLiveError(f"canary did not converge within {max_passes} passes")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="phase2_canary_live")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--limit", type=int, default=None,
                        help="attempt at most N incomplete cells this invocation (smoke)")
    args = parser.parse_args(argv)
    try:
        outcome = run_live(args.manifest, args.authorization, args.project_root,
                           limit=args.limit)
    except (CanaryLiveError, Exception) as exc:  # noqa: BLE001 - report, never swallow
        print(f"REFUSED/HALTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "completed": outcome.completed, "skipped": outcome.skipped,
        "deferred": outcome.deferred, "paused": outcome.paused,
        "halted_reason": outcome.halted_reason,
        "halted_cell_key": outcome.halted_cell_key}, sort_keys=True))
    return 0 if outcome.halted_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
