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
from rejudge.phase2_call_cache import CallCache, CallKey
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_canary_manifest import validate_canary_manifest
from rejudge.phase2_canary_runner import RunOutcome, run_canary
from rejudge.phase2_dual_gate import DualGateDecisionStore, parse_reviewer_output
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


# The single-context equivalent of the API design's system/user role boundary: a frozen
# content-neutral preamble (tool prohibition and output discipline only), the frozen prompt,
# one separator line, the payload. Nothing else. All three constants are recorded verbatim
# in the reviewer transport decision artifact; per-review zero-tool-use is verified from the
# agent runtime's reported usage metadata.
SUBAGENT_PREAMBLE = (
    "Do not use any tools. Do not read any files. Answer purely from the text below, and "
    "reply with EXACTLY the three lines the instructions specify, nothing else.")
SUBAGENT_PAYLOAD_SEPARATOR = "\n\n=== QUERY PAYLOAD ===\n"
WORKLIST_FILENAME = "reviewer_worklist.json"


class _PauseModeReviewer:
    """In the subagent-batch workflow, an unlabeled payload pauses; a live consult is a bug."""

    def __call__(self, raw_query: str, candidate_a: str, candidate_b: str) -> str:
        raise CanaryLiveError(
            "the batch workflow must never consult a live reviewer; an unlabeled payload "
            "pauses for out-of-band labelling instead")


def compose_subagent_prompt(frozen_prompt: str, *, query: str, candidate_a: str,
                            candidate_b: str) -> str:
    return (SUBAGENT_PREAMBLE + "\n\n" + frozen_prompt + SUBAGENT_PAYLOAD_SEPARATOR
            + render_reviewer_payload(query, candidate_a, candidate_b))


def export_reviewer_worklist(pending_payloads, frozen_prompt: str,
                             worklist_path: Path) -> dict[str, Any]:
    items = []
    for payload in pending_payloads:
        prompt = compose_subagent_prompt(
            frozen_prompt, query=payload["query"], candidate_a=payload["candidate_a"],
            candidate_b=payload["candidate_b"])
        items.append({
            "payload_sha256": payload["payload_sha256"],
            "query": payload["query"],
            "candidate_a": payload["candidate_a"],
            "candidate_b": payload["candidate_b"],
            "subagent_prompt": prompt,
            "subagent_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        })
    worklist = {
        "frozen_prompt_sha256": hashlib.sha256(frozen_prompt.encode("utf-8")).hexdigest(),
        "separator": SUBAGENT_PAYLOAD_SEPARATOR,
        "items": items,
    }
    worklist_path.write_text(
        json.dumps(worklist, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
        newline="")
    return worklist


def commit_reviewer_decisions(manifest_path: str | Path, decisions_file: str | Path,
                              project_root: str | Path = ".") -> dict[str, int]:
    """Commit out-of-band reviewer outputs into the hash-chained store.

    Only payloads named by the current worklist are accepted, so a typo in a hash cannot
    plant a decision for a payload no cell proposed. Parse failure commits as ``malformed``
    (non-ALLOW), never as a skip: the frozen failure rule makes unparseable output a
    decision, not an absence.
    """
    manifest = _load_json(Path(manifest_path))
    validate_canary_manifest(manifest, project_root=project_root)
    archive_dir = local_path(manifest["ledger"]["archive_dir"])
    worklist = _load_json(archive_dir / WORKLIST_FILENAME)
    store = DualGateDecisionStore(local_path(manifest["ledger"]["decisions_path"]))
    return commit_decisions_into(store, worklist, _load_json(Path(decisions_file)))


def commit_decisions_into(store: DualGateDecisionStore, worklist: Mapping[str, Any],
                          entries) -> dict[str, int]:
    """Commit out-of-band rulings, refusing any whose prompt bytes are unproven.

    Incident canary_reviewer_prompt_contamination_2026-07-29: a ruling is only evidence
    about the payload the reviewer actually saw. Every entry must therefore carry
    ``prompt_sha256``, the hash of the exact prompt string dispatched, and it must equal
    the worklist's ``subagent_prompt_sha256`` for that payload. A ``reviewer_error`` entry
    is the sole exemption, because no reviewer was consulted at all.
    """
    known = {item["payload_sha256"] for item in worklist["items"]}
    expected_prompt = {item["payload_sha256"]: item.get("subagent_prompt_sha256")
                       for item in worklist["items"]}
    counts = {"parsed": 0, "malformed": 0, "reviewer_error": 0}
    for entry in entries:
        sha = entry["payload_sha256"]
        if sha not in known:
            raise CanaryLiveError(
                f"decision for unknown payload {sha}: not in the current worklist")
        raw_output = entry["raw_output"]
        if entry.get("status") != "reviewer_error":
            proof = entry.get("prompt_sha256")
            if not proof:
                raise CanaryLiveError(
                    f"decision for {sha} carries no prompt_sha256; a ruling whose prompt "
                    "bytes are unproven cannot be committed")
            if proof != expected_prompt.get(sha):
                raise CanaryLiveError(
                    f"decision for {sha} was produced from the wrong prompt bytes: "
                    f"dispatched {proof}, frozen {expected_prompt.get(sha)}")
        if entry.get("status") == "reviewer_error":
            # The reviewer could not be consulted for this payload (frozen failure rule:
            # unavailability commits as non-ALLOW); raw_output preserves the evidence.
            store.commit(sha, None, None, None, raw_output, "reviewer_error")
            counts["reviewer_error"] += 1
            continue
        label, clause, rationale = parse_reviewer_output(raw_output)
        status = "parsed" if label is not None else "malformed"
        store.commit(sha, label, clause, rationale, raw_output, status)
        counts[status] += 1
    return counts


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
# Pinned by the owner on 2026-08-01 and recorded in the reviewer-substitution deviation.
# Reasoning effort is a model-VISIBLE generation setting, so it belongs in the bound record
# exactly as the frozen roster already pins reasoning_effort for openai/gpt-oss-120b.
REVIEWER_REASONING_EFFORT = "high"


class GPTReviewer:
    """The blinded second gate, run against an OpenAI reasoning model.

    Structurally identical to :class:`ClaudeReviewer`: one fresh request per payload
    carrying exactly the frozen prompt and the payload, no conversation state, no tools, no
    metadata. Bounded retries on transient transport failures; a final failure raises, and
    the dual gate commits it as ``reviewer_error`` -> non-ALLOW per the frozen failure rule.

    The wire shape is the Responses API with an explicit reasoning effort. Because this
    model was substituted mid-canary under an owner deviation rather than validated during
    design, the operator MUST run :meth:`probe` once against the live endpoint and inspect
    the result before dispatching a batch.
    """

    def __init__(self, *, prompt: str, model: str, api_key: str,
                 effort: str = REVIEWER_REASONING_EFFORT,
                 timeout: float = REVIEWER_TIMEOUT_SECONDS,
                 max_attempts: int = REVIEWER_MAX_ATTEMPTS,
                 transport=None, sleep=time.sleep) -> None:
        if not api_key:
            raise CanaryLiveError("reviewer requires a non-empty API key")
        if not model:
            raise CanaryLiveError("reviewer requires an explicit model identifier")
        self._prompt = prompt
        self._model = model
        self._api_key = api_key
        self._effort = effort
        self._timeout = float(timeout)
        self._max_attempts = int(max_attempts)
        self._transport = transport if transport is not None else self._http_post
        self._sleep = sleep
        self.calls = 0

    def _http_post(self, body: dict) -> dict:
        request = urllib.request.Request(
            OPENAI_RESPONSES_URL, method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self._api_key}"})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _body(self, raw_query: str, candidate_a: str, candidate_b: str) -> dict:
        return {
            "model": self._model,
            "reasoning": {"effort": self._effort},
            "input": [
                {"role": "developer", "content": self._prompt},
                {"role": "user", "content": render_reviewer_payload(
                    raw_query, candidate_a, candidate_b)},
            ],
        }

    @staticmethod
    def extract_text(data: Mapping[str, Any]) -> str:
        """Pull the assistant text out of a Responses payload, tolerating shape variation."""
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        chunks = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") == "reasoning":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        if not chunks:
            raise CanaryLiveError("no assistant text found in the reviewer response")
        return "".join(chunks)

    def probe(self) -> dict:
        """One live call whose raw response the operator inspects before any batch."""
        return self._transport(self._body("CLAIM: probe.", "A", "B"))

    def __call__(self, raw_query: str, candidate_a: str, candidate_b: str) -> str:
        body = self._body(raw_query, candidate_a, candidate_b)
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                self.calls += 1
                return str(self.extract_text(self._transport(body)))
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


LEDGER_BINDING_FILENAME = "canary_ledger_binding.json"
RUN_LOCK_FILENAME = "canary.lock"
INCIDENT1_RELATIVE_PATH = Path("rejudge/phase2_canary_incident1_2026-07-28.json")
INCIDENT1_SUFFIX = ".incident1-2026-07-28"


class AnotherProcessHoldsTheLock(CanaryLiveError):
    """Raised when a second live process would otherwise write the same archive."""


class _ArchiveLock:
    """Exclusive, non-blocking flock over an archive-side lockfile.

    Held for the whole life of the run (or supervisor). A second process refuses loudly
    instead of blocking: silent queueing is how the 2026-07-28 concurrent-writer incident
    would have re-occurred with extra steps.
    """

    def __init__(self, path: Path, role: str) -> None:
        self._path = path
        self._role = role
        self._handle = None

    def __enter__(self):
        import fcntl
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise AnotherProcessHoldsTheLock(
                f"another {self._role} process holds {self._path}; refusing to run "
                "concurrently (see the 2026-07-28 incident record)") from exc
        self._handle.truncate(0)
        self._handle.write(f"pid={os.getpid()}\n")
        self._handle.flush()
        return self

    def __exit__(self, *exc_info):
        import fcntl
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
        return False

# judge_loop names the oracle role by what the call does; the frozen role-limits artifact
# names it by the role taxonomy. Same call, one alias.
_ROLE_ALIASES = {"oracle_verification": "oracle"}


class RoleLimitResolvingClient:
    """Send the manifest-bound effective_request_max_tokens for every (model, role).

    The frozen v5 role-limits artifact records both the scientific role budget
    (``base_role_max_tokens``, what the frozen call sites request) and the transport value
    actually sent for reasoning models (``effective_request_max_tokens``, at or above the
    strict client's floor). The strict client refuses to floor silently, so this wrapper
    substitutes the artifact's effective value, refusing any (model, role) pair the artifact
    does not list and any requested value that is neither the base nor the effective one.
    The checker adapter already resolves its own effective value, which is why
    already-effective requests pass through.
    """

    def __init__(self, inner, model_role_limits: Mapping[str, Any]) -> None:
        self.inner = inner
        self._limits = {model: dict(roles) for model, roles in model_role_limits.items()}

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None):
        limits = self._limits.get(model)
        if limits is not None:
            raw_role = (request_metadata or {}).get("call_role")
            role = _ROLE_ALIASES.get(raw_role, raw_role)
            entry = limits.get(role)
            if entry is None:
                raise CanaryLiveError(
                    f"call_role {raw_role!r} for model {model!r} is not in the frozen "
                    "role-limits artifact; refusing an unanticipated call shape")
            base = int(entry["base_role_max_tokens"])
            effective = int(entry["effective_request_max_tokens"])
            if int(max_tokens) not in (base, effective):
                raise CanaryLiveError(
                    f"requested max_tokens {max_tokens} for ({model!r}, {role!r}) is neither "
                    f"the frozen base {base} nor the frozen effective {effective}")
            max_tokens = effective
        return self.inner.complete(
            messages, model, temperature, seed, max_tokens, kind=kind,
            request_metadata=request_metadata)


def carried_forward_spend(binding: Mapping[str, Any]) -> tuple[float, float]:
    """The incident-window spend a migrated ledger carries into the cap arithmetic."""
    carried = binding.get("carried_forward", {})
    return (float(carried.get("actual_spend_usd", 0.0)),
            float(carried.get("uncertain_spend_usd", 0.0)))


def conservative_ledger_spend(path: Path) -> tuple[float, float]:
    """Chain-agnostic conservative read of an interleaved ledger: every success is actual;
    every unknown_charge and every reservation without a terminal is uncertain."""
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
              if l.strip()]
    terminal = set()
    for event in events:
        if event.get("status") in ("success", "released_no_charge", "charged_malformed",
                                   "unknown_charge"):
            terminal.add(str(event.get("attempt_id")))
    actual = sum(float(e.get("cost_usd", 0)) for e in events if e.get("status") == "success")
    uncertain = sum(
        float(e.get("cost_usd", 0)) for e in events
        if e.get("status") == "unknown_charge"
        or (e.get("status") == "reserved" and str(e.get("attempt_id")) not in terminal))
    return actual, uncertain


def migrate_interleaved_ledger(manifest: Mapping[str, Any], *,
                               project_root: str | Path) -> dict:
    """Explicit recovery from the 2026-07-28 concurrent-writer incident.

    Preserves the interleaved usage ledger and call cache byte-for-byte under the incident
    suffix, creates a fresh ledger genesis bound to the manifest with the conservative
    incident-window spend carried forward, and rebuilds the cache deterministically from the
    preserved rows through a fresh chain (no provider calls). Refuses without the committed
    incident record: an undocumented migration is exactly the silent adoption the doctrine
    prohibits.
    """
    root = Path(project_root)
    if not (root / INCIDENT1_RELATIVE_PATH).exists():
        raise CanaryLiveError(
            f"migration requires the incident record {INCIDENT1_RELATIVE_PATH} on disk")
    archive_dir = local_path(manifest["ledger"]["archive_dir"])
    usage_path = local_path(manifest["ledger"]["usage_log_path"])
    cache_path = local_path(manifest["ledger"]["call_cache_path"])
    binding_path = archive_dir / LEDGER_BINDING_FILENAME
    with _ArchiveLock(archive_dir / RUN_LOCK_FILENAME, "canary"):
        carried_actual, carried_uncertain = conservative_ledger_spend(usage_path)
        for path in (usage_path, Path(str(usage_path) + ".state.json"), cache_path):
            if path.exists():
                target = Path(str(path) + INCIDENT1_SUFFIX)
                if target.exists():
                    raise CanaryLiveError(f"{target} already exists; migration already ran?")
                path.rename(target)

        identity = api_client.prepare_usage_ledger(usage_path, allow_create=True)
        binding = {
            "schema_version": "phase2_canary_ledger_binding_v2",
            "execution_identity_sha256": manifest["execution_identity_sha256"],
            "ledger_identity": dict(identity),
            "carried_forward": {
                "actual_spend_usd": float(carried_actual),
                "uncertain_spend_usd": float(carried_uncertain),
                "from": str(usage_path) + INCIDENT1_SUFFIX,
                "incident_record": str(INCIDENT1_RELATIVE_PATH),
            },
        }
        binding_path.write_text(
            json.dumps(binding, ensure_ascii=True, sort_keys=True, indent=1) + "\n",
            encoding="utf-8", newline="")

        preserved = Path(str(cache_path) + INCIDENT1_SUFFIX)
        rebuilt = CallCache(cache_path)
        replayed = 0
        for line in preserved.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rebuilt.put(
                CallKey(cell_key=row["cell_key"], call_role=row["call_role"],
                        slot=int(row["slot"]), attempt=int(row["attempt"])),
                row["request_sha256"], row["response"])
            replayed += 1
    return {"cache_rows_rebuilt": replayed, "ledger_identity": dict(identity),
            "carried_forward": binding["carried_forward"]}


INCIDENT2_RELATIVE_PATH = Path("rejudge/phase2_canary_incident2_2026-07-29.json")
INCIDENT2_SUFFIX = ".incident2-2026-07-29"


def rebuild_decision_store(manifest: Mapping[str, Any], *, project_root: str | Path,
                           verified_path: str | Path) -> dict:
    """Retire a contaminated decision store and rebuild it from verified rulings only.

    Incident canary_reviewer_prompt_contamination_2026-07-29 left six rulings in an
    append-only store that refuses a second commit per payload, so they cannot be corrected
    in place. Under owner sign-off this preserves the original store byte-for-byte as
    evidence and writes a fresh hash-chained store containing only rulings whose dispatched
    prompt bytes were proven against the frozen payload.

    Refuses without the committed incident record, and refuses if any excluded payload
    would survive into the successor store.
    """
    root = Path(project_root)
    if not (root / INCIDENT2_RELATIVE_PATH).exists():
        raise CanaryLiveError(
            f"rebuild requires the incident record {INCIDENT2_RELATIVE_PATH} on disk")
    payload = _load_json(Path(verified_path))
    verified, excluded = payload["verified"], payload["excluded"]
    excluded_shas = {e["payload_sha256"] for e in excluded}
    if excluded_shas & {v["payload_sha256"] for v in verified}:
        raise CanaryLiveError("an excluded payload also appears in the verified set")
    for v in verified:
        if v["status"] != "reviewer_error" and not v.get("prompt_sha256"):
            raise CanaryLiveError(
                f"verified ruling {v['payload_sha256']} carries no prompt proof")

    decisions_path = local_path(manifest["ledger"]["decisions_path"])
    archive_dir = local_path(manifest["ledger"]["archive_dir"])
    with _ArchiveLock(archive_dir / RUN_LOCK_FILENAME, "canary"):
        retired = Path(str(decisions_path) + INCIDENT2_SUFFIX)
        if retired.exists():
            raise CanaryLiveError(f"{retired} already exists; rebuild already ran?")
        if decisions_path.exists():
            decisions_path.rename(retired)
        store = DualGateDecisionStore(decisions_path)
        for v in verified:
            store.commit(v["payload_sha256"], v["label"], v["clause"], v["rationale"],
                         v["raw_output"], v["status"])
        rebuilt = DualGateDecisionStore(decisions_path)   # re-read: proves the chain loads
        for sha in excluded_shas:
            if rebuilt.get(sha) is not None:
                raise CanaryLiveError(f"excluded payload {sha} survived into the rebuild")
    return {"retired_to": str(retired), "rebuilt_rulings": len(verified),
            "excluded": sorted(excluded_shas)}


def supersede_ledger_binding(manifest: Mapping[str, Any], *, project_root: str | Path,
                             reason_tracked_path: str) -> dict:
    """Point an existing ledger binding at an amended manifest, recording the chain.

    A mid-run manifest amendment (the 2026-08-01 reviewer substitution) changes the execution
    identity while the physical run, its ledger, cache and results continue. Rewriting the
    binding silently would erase the fact that the run changed shape underneath, so the prior
    identity is retained in ``superseded_identities`` together with the artifact that
    justifies the change. Refuses unless that artifact exists.
    """
    root = Path(project_root)
    if not (root / reason_tracked_path).exists():
        raise CanaryLiveError(
            f"supersession requires the justifying record {reason_tracked_path} on disk")
    binding_path = local_path(manifest["ledger"]["archive_dir"]) / LEDGER_BINDING_FILENAME
    if not binding_path.exists():
        raise CanaryLiveError(f"no ledger binding to supersede at {binding_path}")
    binding = _load_json(binding_path)
    new_identity = manifest["execution_identity_sha256"]
    current = binding.get("execution_identity_sha256")
    if current == new_identity:
        return {"unchanged": True, "execution_identity_sha256": new_identity}
    chain = list(binding.get("superseded_identities") or [])
    chain.append({"execution_identity_sha256": current,
                  "superseded_by_record": reason_tracked_path})
    binding["superseded_identities"] = chain
    binding["execution_identity_sha256"] = new_identity
    binding["schema_version"] = "phase2_canary_ledger_binding_v3"
    binding_path.write_text(
        json.dumps(binding, ensure_ascii=True, sort_keys=True, indent=1) + "\n",
        encoding="utf-8", newline="")
    return {"unchanged": False, "previous": current, "now": new_identity,
            "chain_length": len(chain)}


def bind_or_verify_ledger(manifest: Mapping[str, Any], usage_log_path: Path,
                          binding_path: Path) -> dict[str, Any]:
    """First run: create the ledger and bind its genesis identity to this manifest.

    Resume: require the binding, require it to name this manifest's execution identity, and
    require the on-disk ledger to carry exactly the bound genesis identity. A paid ledger
    with no binding is refused, never adopted silently; adoption after a crash between
    ledger creation and binding publication is an explicit operator reconciliation step.
    """
    if binding_path.exists():
        binding = _load_json(binding_path)
        if binding.get("execution_identity_sha256") != manifest["execution_identity_sha256"]:
            raise CanaryLiveError(
                f"ledger binding {binding_path} names a different execution identity")
        api_client.prepare_usage_ledger(usage_log_path, allow_create=False)
        return dict(binding["ledger_identity"])
    # (v1 bindings have no carried_forward; carried_forward_spend reads zeros.)
    identity = api_client.prepare_usage_ledger(usage_log_path, allow_create=True)
    binding = {
        "schema_version": "phase2_canary_ledger_binding_v1",
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "ledger_identity": dict(identity),
    }
    binding_path.write_text(
        json.dumps(binding, ensure_ascii=True, sort_keys=True, indent=1) + "\n",
        encoding="utf-8", newline="")
    return dict(identity)


STREAMING_DEVIATION_RELATIVE_PATH = Path(
    "rejudge/phase2_canary_streaming_deviation_2026-07-28.json")
TERMINAL_HALTS_RELATIVE_PATH = Path(
    "rejudge/phase2_canary_terminal_halts_2026-07-29.json")


def load_terminal_halt_cells(project_root: str | Path, results_path: Path) -> frozenset[str]:
    """Cells recorded as terminally halted (Consult #28 scope): skipped, never resumed.

    Refuses if a listed cell already has a result row: a completed cell cannot also be
    terminally halted, so the disposition would be stale.
    """
    path = Path(project_root) / TERMINAL_HALTS_RELATIVE_PATH
    if not path.exists():
        return frozenset()
    record = _load_json(path)
    if record.get("schema_version") != "phase2_canary_terminal_halts_v1":
        raise CanaryLiveError(f"unrecognized terminal-halts record at {path}")
    cells = frozenset(str(entry["cell_key"]) for entry in record["cells"])
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("cell_key") in cells:
                raise CanaryLiveError(
                    "a terminally halted cell already has a result row; the disposition "
                    "record is stale")
    return cells


def _apply_streaming_deviation(pinned: frozenset[str],
                               project_root: Path) -> frozenset[str]:
    """Apply the recorded gpt-oss streaming deviation, only if its record exists.

    Together currently 5xxes the frozen stream+reasoning_effort combination for
    gpt-oss-120b (see the record for probe evidence). The record is the authority: no
    record, no override; a record naming an unpinned model refuses, so this can never
    silently widen.
    """
    path = project_root / STREAMING_DEVIATION_RELATIVE_PATH
    if not path.exists():
        return pinned
    record = _load_json(path)
    if record.get("schema_version") != "phase2_canary_streaming_deviation_v1":
        raise CanaryLiveError(f"unrecognized streaming deviation record at {path}")
    model = "openai/gpt-oss-120b"
    if model not in pinned:
        raise CanaryLiveError(
            "streaming deviation record present but its model is not stream-pinned; "
            "the pins and the record have drifted apart")
    return frozenset(pinned - {model})


def build_live_client(manifest: Mapping[str, Any], *, project_root: str | Path,
                      usage_log_path: Path, error_log_path: Path,
                      call_cache_path: Path) -> CachingClient:
    root = Path(project_root)
    role_limits = _load_json(root / "rejudge" / "phase2_role_limits_v5_2026-07-19.json")
    snapshot = _load_json(
        root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json")
    limits, pinned, extra_fields, prices = _client_construction_inputs(role_limits, snapshot)
    pinned = _apply_streaming_deviation(pinned, root)
    transport = role_limits["request_settings"]["transport"]

    binding_path = usage_log_path.parent / LEDGER_BINDING_FILENAME
    identity = bind_or_verify_ledger(manifest, usage_log_path, binding_path)
    carried_actual, carried_uncertain = carried_forward_spend(
        _load_json(binding_path) if binding_path.exists() else {})
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
        initial_spend_usd=float(ledger_snapshot.summary["actual_spend_usd"]) + carried_actual,
        initial_uncertain_spend_usd=(
            float(ledger_snapshot.summary["uncertain_spend_usd"]) + carried_uncertain),
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
    resolving = RoleLimitResolvingClient(client, role_limits["model_role_limits"])
    return CachingClient(resolving, CallCache(call_cache_path))


def run_live(manifest_path: str | Path, authorization_path: str | Path,
             project_root: str | Path = ".", *, limit: int | None = None,
             max_passes: int = 40, client=None, reviewer=None,
             mode: str = "api") -> RunOutcome:
    """Execute the authorized canary. The only entry point here that can spend money.

    ``mode="api"`` reviews each payload inline over the Anthropic API. In
    ``mode="subagent-batch"`` no live reviewer exists: an unlabeled payload pauses its
    cell, the accumulated payloads are exported as a worklist of mechanically composed
    single-context prompts (frozen prompt + separator + payload, nothing else), and the
    run returns so an out-of-band claude-fable-5 subagent batch can label them; committed
    decisions are then picked up by the next invocation. See the reviewer transport
    decision artifact for why both mechanisms satisfy the amendment's isolation floor.

    ``client``/``reviewer`` are injectable for offline tests only.
    """
    if mode not in ("api", "subagent-batch"):
        raise CanaryLiveError(f"unknown mode {mode!r}")
    if authorization_path is None:
        raise CanaryLiveError("run_live requires an authorization_path; there is no bypass")
    manifest = _load_json(Path(manifest_path))
    validate_canary_manifest(manifest, project_root=project_root)
    authorization = _load_json(Path(authorization_path))
    validate_canary_authorization(authorization, manifest, project_root=project_root)

    frozen = load_frozen_reviewer_prompt(manifest, project_root)
    pause_when_unlabeled = mode == "subagent-batch"
    if reviewer is None:
        if mode == "api":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise CanaryLiveError(
                    "ANTHROPIC_API_KEY is missing or blank; the reviewer gate cannot run")
            reviewer = ClaudeReviewer(prompt=frozen["prompt"], model=frozen["model"],
                                      api_key=api_key)
        else:
            reviewer = _PauseModeReviewer()
    lock = None
    if client is None:
        if not os.environ.get("TOGETHER_API_KEY", "").strip():
            raise CanaryLiveError(
                "TOGETHER_API_KEY is missing or blank; refusing before any construction")
        archive_dir = local_path(manifest["ledger"]["archive_dir"])
        archive_dir.mkdir(parents=True, exist_ok=True)
        lock = _ArchiveLock(archive_dir / RUN_LOCK_FILENAME, "canary").__enter__()
        client = build_live_client(
            manifest, project_root=project_root,
            usage_log_path=local_path(manifest["ledger"]["usage_log_path"]),
            error_log_path=archive_dir / "canary_error_log.jsonl",
            call_cache_path=local_path(manifest["ledger"]["call_cache_path"]))

    results_path = local_path(manifest["ledger"]["results_path"])
    decisions_path = local_path(manifest["ledger"]["decisions_path"])

    terminal = load_terminal_halt_cells(project_root, results_path)
    try:
        return _run_passes(manifest, client=client, reviewer=reviewer,
                           results_path=results_path, decisions_path=decisions_path,
                           limit=limit, max_passes=max_passes,
                           pause_when_unlabeled=pause_when_unlabeled,
                           terminal_halt_cells=terminal)
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


def _run_passes(manifest, *, client, reviewer, results_path, decisions_path, limit,
                max_passes, pause_when_unlabeled,
                terminal_halt_cells: frozenset[str] = frozenset()) -> RunOutcome:
    frozen = load_frozen_reviewer_prompt(manifest)
    cell_filter = None
    if terminal_halt_cells:
        # The exclusion cascades: a cell whose dependency is terminally halted can never
        # complete (its replay consumes exchanges that will never exist), so it is excluded
        # mechanically and reported as dependency_terminally_halted at close-out.
        def cell_filter(cell):
            if cell.cell_key in terminal_halt_cells:
                return False
            return not (set(getattr(cell, "dependency_keys", ()) or ())
                        & terminal_halt_cells)
    outcome = RunOutcome()
    for pass_index in range(1, max_passes + 1):
        outcome = run_canary(
            results_path=results_path, decisions_path=decisions_path, client=client,
            reviewer=reviewer, anchor_judge_model=str(manifest["anchor"]["judge_model"]),
            pause_when_unlabeled=pause_when_unlabeled, limit=limit,
            cell_filter=cell_filter)
        print(f"pass {pass_index}: completed={outcome.completed} "
              f"skipped={outcome.skipped} deferred={outcome.deferred} "
              f"paused={outcome.paused} halted={outcome.halted_reason or '-'}",
              flush=True)
        if outcome.halted_reason is not None or limit is not None:
            break
        if outcome.needs_labelling:
            break
        if outcome.deferred == 0:
            return outcome
        if outcome.completed == 0:
            raise CanaryLiveError(
                f"no progress: {outcome.deferred} cells still deferred after a full pass")
    else:
        raise CanaryLiveError(f"canary did not converge within {max_passes} passes")

    if outcome.needs_labelling and pause_when_unlabeled:
        worklist_path = local_path(manifest["ledger"]["archive_dir"]) / WORKLIST_FILENAME
        export_reviewer_worklist(outcome.pending_payloads, frozen["prompt"], worklist_path)
        print(f"exported {len(outcome.pending_payloads)} pending payloads to "
              f"{worklist_path}", flush=True)
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="phase2_canary_live")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--mode", choices=("api", "subagent-batch"), default="api")
    parser.add_argument("--limit", type=int, default=None,
                        help="attempt at most N incomplete cells this invocation (smoke)")
    parser.add_argument("--commit-decisions", default=None,
                        help="commit a JSON file of out-of-band reviewer outputs and exit")
    parser.add_argument("--migrate-interleaved-ledger", action="store_true",
                        help="explicit incident recovery; requires the incident record")
    args = parser.parse_args(argv)
    try:
        if args.migrate_interleaved_ledger:
            manifest = _load_json(Path(args.manifest))
            validate_canary_manifest(manifest, project_root=args.project_root)
            summary = migrate_interleaved_ledger(manifest, project_root=args.project_root)
            print(json.dumps(summary, sort_keys=True))
            return 0
        if args.commit_decisions is not None:
            counts = commit_reviewer_decisions(
                args.manifest, args.commit_decisions, args.project_root)
            print(json.dumps(counts, sort_keys=True))
            return 0
        outcome = run_live(args.manifest, args.authorization, args.project_root,
                           limit=args.limit, mode=args.mode)
    except (CanaryLiveError, Exception) as exc:  # noqa: BLE001 - report, never swallow
        print(f"REFUSED/HALTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "completed": outcome.completed, "skipped": outcome.skipped,
        "deferred": outcome.deferred, "paused": outcome.paused,
        "pending_labels": len(outcome.pending_payloads),
        "halted_reason": outcome.halted_reason,
        "halted_cell_key": outcome.halted_cell_key}, sort_keys=True))
    return 0 if outcome.halted_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
