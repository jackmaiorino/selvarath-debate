"""Amendment 4 (2026-08-19), package item 3: empirically validate the context-only estimator.

Freezes a prefix of the live usage ledger (sequence, terminal event hash, raw sha256 of the
exact bytes read) and, for every TERMINAL call in it that carries a provider-reported
``prompt_tokens`` (i.e. ``status`` is ``success`` or ``charged_malformed``), checks the
never-underestimate requirement the Codex consult prescribes
(rejudge/phase3_codex_context_guard_consult_2026-08-19.md, "2."):

    E_prompt >= actual_prompt_tokens + max(512, ceil(0.25 * actual_prompt_tokens))

**No raw request text is stored in the usage ledger** (by design -- it would be enormous and is
not needed for accounting), so ``E_prompt`` cannot be recomputed from the real message bytes
directly. Instead this script recovers the SAME quantity the live guard would have used,
starting from the ledger's own byte-conservative reservation estimate
(``rejudge.api_client._estimate_usage``, recorded as each event's ``estimated_tokens``):

    estimated_tokens          = estimated_prompt_bytes(old) + reserved_completion
    reserved_completion       = effective_max_tokens * COMPLETION_RESERVE_MULTIPLIER (reasoning
                                 models) or effective_max_tokens (every other model) -- both
                                 exactly recoverable from the model/call_role and the frozen
                                 role-limits artifact, no approximation
    estimated_prompt_bytes(old) = 64 + 32*n_messages + prompt_utf8_bytes

The only unrecoverable term is ``n_messages`` (the exact message count is not itself logged).
Rather than guess it, this script substitutes a PROVEN UPPER BOUND on ``n_messages`` for any
phase-3 sequential-judgment call under the frozen protocol's own constants (max query_budget,
:data:`MAX_ATTEMPTS_PER_SLOT`): ``MAX_PLAUSIBLE_MESSAGES`` below. Subtracting a bound that is
>= the true message count can only ever UNDER-estimate ``prompt_utf8_bytes`` -- never over --
so the ``E_prompt`` this script computes is <= the true, exact ``E_prompt`` a full message
reconstruction would find. A PASS under this conservative substitute is therefore a strictly
STRONGER guarantee than checking the true value: if the deliberately-shrunk estimate still
clears the required margin, the true (larger) one clears it by at least as much. A FAILURE
under this substitute is not automatically a genuine miss -- see the report's own
``methodology`` block -- and must be investigated against the real request bytes before being
treated as a formula failure; this script never suppresses one to make the report look better.

CLI: ``--ledger`` (the live, READ-ONLY usage ledger; may still be growing -- the bytes are read
ONCE and hashed, so "the frozen prefix" is exactly what this invocation observed), ``--protocol``,
``--role-limits``, ``--out``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.api_client import (  # noqa: E402
    COMPLETION_RESERVE_MULTIPLIER,
    CONTEXT_ESTIMATOR_BYTES_PER_TOKEN_DIVISOR,
    CONTEXT_ESTIMATOR_PROMPT_MARGIN_TOKENS,
    _usage_event_hash,
    _validate_usage_chain,
)
from rejudge.judge_loop import _MAX_ATTEMPTS_PER_SLOT  # noqa: E402
from rejudge.phase2_canary_live import _ROLE_ALIASES  # noqa: E402
from rejudge.phase3_plan import load_protocol  # noqa: E402

# _estimate_usage's own fixed byte-padding constants (rejudge/api_client.py): 64 base bytes,
# plus 32 bytes of framing per message. Duplicated here as named constants (not imported
# privately a second time) so this script's own arithmetic is self-contained and readable.
_ESTIMATE_USAGE_BASE_BYTES = 64
_ESTIMATE_USAGE_PER_MESSAGE_BYTES = 32

# TERMINAL statuses that carry genuine provider-reported usage, per rejudge.api_client's own
# lifecycle (_summarize_usage_events): "success" and "charged_malformed" are billed and their
# prompt_tokens/completion_tokens are real; "unknown_charge" has no token ground truth (the
# billing outcome itself is ambiguous) and is reported separately, never checked; "reserved" is
# not yet terminal at all.
_GROUND_TRUTH_STATUSES = frozenset({"success", "charged_malformed"})

# Every status rejudge.api_client._summarize_usage_events treats as terminal (matching a
# "reserved" event and closing it out), including the two this script does not itself check
# (unknown_charge, released_no_charge) -- used ONLY to identify which reservations in the frozen
# prefix have NO matching terminal event at all yet (Codex re-review, 2026-08-19: the prefix's
# chain-tail hash can legitimately belong to a still-open "reserved" event, not a genuinely
# terminal one, and that must be reported honestly rather than implied away by the field name).
_TERMINAL_STATUSES = frozenset({"success", "charged_malformed", "unknown_charge",
                               "released_no_charge"})

REQUIRED_MIN_ABSOLUTE_MARGIN_TOKENS = 512
REQUIRED_MIN_RELATIVE_MARGIN_FRACTION = 0.25


class EstimatorValidationError(ValueError):
    """Raised when the validation cannot proceed at all (not: when a call fails the check)."""


def max_plausible_messages(protocol: Mapping[str, Any]) -> int:
    """A PROVEN upper bound on the message count any phase-3 sequential-judgment call can carry.

    2 (system + presentation) + at most ``_MAX_ATTEMPTS_PER_SLOT`` attempts per query slot, each
    contributing at most 3 messages (query template + assistant response + user feedback/oracle
    result -- judge_loop.run_judgment never appends more than that per attempt), across the
    frozen protocol's largest ``query_budget`` + 1 (the verdict template). Every other call role
    (oracle_verification, query_checker, capability_qa) uses a far shorter, fixed 2-message
    shape, so this bound safely covers them too.
    """
    conditions = protocol["debate_grid"]["conditions"]
    max_budget = max(int(condition["query_budget"]) for condition in conditions)
    return 2 + _MAX_ATTEMPTS_PER_SLOT * max_budget * 3 + 1


def freeze_ledger_prefix(path: Path) -> tuple[list[dict], dict[str, Any]]:
    """Read the ledger's raw bytes ONCE and return ``(usage_events, prefix_binding)``.

    The ledger may still be actively growing (a live run); reading it exactly once and hashing
    exactly those bytes is what makes "the frozen prefix" mean something concrete, independent
    of whatever the file grows to afterward. Chain-validated with the SAME hash-chain check
    ``rejudge.api_client.load_chained_usage_ledger`` uses, reused rather than re-derived.

    ``prefix_chain_tail_hash`` (Codex re-review, 2026-08-19) is honestly named: it is the hash
    of the LAST event in the frozen prefix by chain order, which can legitimately be a
    ``reserved`` event whose attempt was still in flight the instant this prefix was read --
    never claimed to be a genuinely terminal lifecycle event. ``unmatched_reservations_in_prefix``
    lists every such still-open reservation explicitly (never silently absorbed into the PASS),
    so a reader can see exactly which attempts this prefix caught mid-flight.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    _identity, hashes = _validate_usage_chain(events, path)
    usage_events = events[1:]  # events[0] is ledger_genesis, not a usage event

    terminal_attempt_ids = {
        str(event["attempt_id"]) for event in usage_events
        if event.get("status") in _TERMINAL_STATUSES}
    unmatched_reservations = [
        {"attempt_id": event.get("attempt_id"), "sequence": event.get("sequence"),
         "model": event.get("model"),
         "call_role": (event.get("metadata") or {}).get("call_role")}
        for event in usage_events
        if event.get("status") == "reserved"
        and str(event.get("attempt_id")) not in terminal_attempt_ids]

    prefix_binding = {
        "ledger_path": str(path).replace("\\", "/"),
        "last_sequence": int(events[-1]["sequence"]),
        "prefix_chain_tail_hash": hashes[-1],
        "event_count": len(events),
        "raw_byte_count": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "unmatched_reservations_in_prefix": unmatched_reservations,
    }
    return usage_events, prefix_binding


def resolve_effective_max_tokens(role_limits: Mapping[str, Any], model: str,
                                 call_role: str) -> int:
    canonical_role = _ROLE_ALIASES.get(call_role, call_role)
    entry = (role_limits.get("model_role_limits") or {}).get(model, {}).get(canonical_role)
    if entry is None:
        raise EstimatorValidationError(
            f"role-limits artifact has no ({model!r}, {canonical_role!r}) entry")
    return int(entry["effective_request_max_tokens"])


def reserved_completion_for(role_limits: Mapping[str, Any], model: str,
                            effective_max_tokens: int) -> int:
    reasoning_ids = frozenset((role_limits.get("reasoning_models") or {}).get("model_ids") or ())
    if model in reasoning_ids:
        return effective_max_tokens * COMPLETION_RESERVE_MULTIPLIER
    return effective_max_tokens


def conservative_prompt_bytes_lower_bound(estimated_prompt_old: int,
                                          max_messages: int) -> int:
    """A safe (never-over) lower bound on the true ``prompt_utf8_bytes`` for one call.

    ``estimated_prompt_old = 64 + 32*n + prompt_utf8_bytes`` for the TRUE ``n``; subtracting the
    largest ``n`` could plausibly be (:func:`max_plausible_messages`) can only remove AT LEAST
    as much as the real padding term removes, so the result is <= the true
    ``prompt_utf8_bytes``.
    """
    return max(0, estimated_prompt_old - _ESTIMATE_USAGE_BASE_BYTES
               - _ESTIMATE_USAGE_PER_MESSAGE_BYTES * max_messages)


def conservative_e_prompt(estimated_prompt_old: int, max_messages: int) -> int:
    prompt_bytes_lb = conservative_prompt_bytes_lower_bound(estimated_prompt_old, max_messages)
    return (math.ceil(prompt_bytes_lb / CONTEXT_ESTIMATOR_BYTES_PER_TOKEN_DIVISOR)
           + CONTEXT_ESTIMATOR_PROMPT_MARGIN_TOKENS)


def required_e_prompt_floor(actual_prompt_tokens: int) -> int:
    return actual_prompt_tokens + max(
        REQUIRED_MIN_ABSOLUTE_MARGIN_TOKENS,
        math.ceil(REQUIRED_MIN_RELATIVE_MARGIN_FRACTION * actual_prompt_tokens))


def validate(events: list[dict], *, role_limits: Mapping[str, Any],
            max_messages: int) -> dict[str, Any]:
    checked = 0
    misses: list[dict[str, Any]] = []
    unknown_charge_calls = 0
    min_absolute_headroom: int | None = None
    min_relative_headroom: float | None = None
    min_headroom_call: dict[str, Any] | None = None

    for event in events:
        status = event.get("status")
        if status == "unknown_charge":
            unknown_charge_calls += 1
            continue
        if status not in _GROUND_TRUTH_STATUSES:
            continue
        actual_prompt = event.get("prompt_tokens")
        if not isinstance(actual_prompt, int) or isinstance(actual_prompt, bool):
            continue

        model = str(event["model"])
        metadata = event.get("metadata") or {}
        call_role = str(metadata.get("call_role"))
        effective_max_tokens = resolve_effective_max_tokens(role_limits, model, call_role)
        reserved_completion = reserved_completion_for(role_limits, model, effective_max_tokens)
        estimated_prompt_old = int(event["estimated_tokens"]) - reserved_completion
        e_prompt = conservative_e_prompt(estimated_prompt_old, max_messages)
        required = required_e_prompt_floor(actual_prompt)
        headroom_abs = e_prompt - required
        headroom_rel = (e_prompt - actual_prompt) / actual_prompt if actual_prompt > 0 else None

        checked += 1
        if min_absolute_headroom is None or headroom_abs < min_absolute_headroom:
            min_absolute_headroom = headroom_abs
            min_headroom_call = {
                "attempt_id": event.get("attempt_id"), "sequence": event.get("sequence"),
                "model": model, "call_role": call_role,
                "cell_key": metadata.get("cell_key"),
            }
        if headroom_rel is not None and (
                min_relative_headroom is None or headroom_rel < min_relative_headroom):
            min_relative_headroom = headroom_rel

        if headroom_abs < 0:
            misses.append({
                "attempt_id": event.get("attempt_id"), "sequence": event.get("sequence"),
                "model": model, "call_role": call_role, "cell_key": metadata.get("cell_key"),
                "actual_prompt_tokens": actual_prompt, "e_prompt_conservative": e_prompt,
                "required_floor": required, "headroom_tokens": headroom_abs,
            })

    return {
        "calls_checked": checked,
        "misses": misses,
        "unknown_charge_calls": unknown_charge_calls,
        "min_absolute_headroom_tokens": min_absolute_headroom,
        "min_relative_headroom_fraction": min_relative_headroom,
        "min_headroom_call": min_headroom_call,
    }


def build_report(ledger_path: Path, *, protocol: Mapping[str, Any],
                 role_limits: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    events, prefix_binding = freeze_ledger_prefix(ledger_path)
    max_messages = max_plausible_messages(protocol)
    result = validate(events, role_limits=role_limits, max_messages=max_messages)
    return {
        "generated_at": generated_at,
        "schema_version": "phase3_estimator_validation_v1",
        "prefix_binding": prefix_binding,
        "estimator": {
            "module": "rejudge.api_client", "function": "estimate_context_tokens",
            "bytes_per_token_divisor": CONTEXT_ESTIMATOR_BYTES_PER_TOKEN_DIVISOR,
            "prompt_margin_tokens": CONTEXT_ESTIMATOR_PROMPT_MARGIN_TOKENS,
        },
        "requirement": {
            "rule": "E_prompt >= actual_prompt_tokens + max(512, ceil(0.25 * actual_prompt_tokens))",
            "min_absolute_margin_tokens": REQUIRED_MIN_ABSOLUTE_MARGIN_TOKENS,
            "min_relative_margin_fraction": REQUIRED_MIN_RELATIVE_MARGIN_FRACTION,
        },
        "methodology": {
            "note": (
                "The usage ledger stores no raw request text, so E_prompt is not recomputed "
                "from real message bytes. It is instead lower-bounded from the ledger's own "
                "byte-conservative reservation estimate (_estimate_usage's estimated_tokens), "
                "peeling off the exactly-known reserved-completion term and a PROVEN upper "
                "bound on message count (max_plausible_messages) in place of the ledger's "
                "unrecorded true message count. Subtracting an upper bound on the message-count "
                "padding term can only underestimate prompt_utf8_bytes, never overestimate it, "
                "so every E_prompt value in this report is <= the true value a full message "
                "reconstruction would compute. A PASS is therefore a strictly stronger guarantee "
                "than checking the true value directly. A reported miss is NOT automatically a "
                "genuine formula failure -- it must be checked against the real request bytes "
                "before being treated as one -- but is reported here regardless, never "
                "suppressed."
            ),
            "max_plausible_messages": max_messages,
        },
        "calls_checked": result["calls_checked"],
        "misses": result["misses"],
        "unknown_charge_calls_reported_separately": result["unknown_charge_calls"],
        "min_absolute_headroom_tokens": result["min_absolute_headroom_tokens"],
        "min_relative_headroom_fraction": result["min_relative_headroom_fraction"],
        "min_headroom_call": result["min_headroom_call"],
    }


def _canonical_bytes(report: Mapping[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True, help="the live, READ-ONLY usage ledger")
    parser.add_argument("--protocol", default=str(REPO_ROOT / "rejudge" / "phase3_protocol.json"))
    parser.add_argument("--role-limits",
                        default=str(REPO_ROOT / "rejudge"
                                   / "phase3_role_limits_v1_2026-08-18.json"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)

    try:
        protocol = load_protocol(args.protocol)
        role_limits = json.loads(Path(args.role_limits).read_text(encoding="utf-8"))
        generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
        report = build_report(Path(args.ledger), protocol=protocol, role_limits=role_limits,
                              generated_at=generated_at)
    except (EstimatorValidationError, KeyError, ValueError, OSError) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(_canonical_bytes(report))

    print(f"calls_checked={report['calls_checked']} misses={len(report['misses'])} "
         f"unknown_charge={report['unknown_charge_calls_reported_separately']} "
         f"min_abs_headroom={report['min_absolute_headroom_tokens']} "
         f"min_rel_headroom={report['min_relative_headroom_fraction']}")
    return 0 if not report["misses"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
