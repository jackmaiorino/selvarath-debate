"""Amendment 4 (2026-08-19), package item 4: carry-forward replay of the visible-history caps.

The estimator change (:mod:`scripts.phase3_estimator_validation`) affects ADMISSION only --
whether a request is dispatched at all -- so a canary cell already completed under the OLD
guard may carry forward into the successor run WITHOUT being replayed live, but only if the NEW
mechanically-enforced visible-history byte cap
(:func:`rejudge.judge_loop.visible_history_cap_bytes`, package item 2) would not have changed
that cell's outcome. This script proves that empirically: it replays every completed canary
``judge_query`` response recorded in the durable call-cache store
(``rejudge.phase2_call_cache.CallCache``) against its class's cap and confirms none of them
would have exceeded it -- i.e. no completed cell's history would have transitioned differently
(retried or blocked instead of allowed) under the new cap.

Judge model per response is resolved from the usage ledger (the call cache itself does not
record which model served a call): every usage-ledger event sharing a response's ``cell_key``
was necessarily served by that cell's one judge model, so the first ledger event found for a
given ``cell_key`` fixes it unambiguously.

Both stores are READ-ONLY inputs; this script writes only its own report.

CLI: ``--call-cache``, ``--usage-ledger``, ``--role-limits``, ``--out``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.judge_loop import (  # noqa: E402
    JUDGE_QUERY_ROLE,
    VisibleHistoryCapClassificationError,
    visible_history_cap_bytes,
)

_CLASS_LABELS = {1024: "base", 6144: "reasoning"}


class HistoryCapReplayError(ValueError):
    """Raised when the replay cannot proceed at all (not: when a response exceeds its cap)."""


# A judgment cell's ledger events are NOT all served by its one judge: the SAME cell_key also
# covers that cell's query_checker calls (the frozen checker model) and oracle_verification
# calls (the frozen oracle model), each a different "model" under the identical cell_key. Only
# these two call roles are ever made BY the judge itself, so the map below is built from those
# alone.
_JUDGE_SERVED_CALL_ROLES = frozenset({JUDGE_QUERY_ROLE, "judge_verdict"})


def build_cell_judge_model_map(usage_ledger_path: Path) -> dict[str, str]:
    """One pass over the usage ledger: ``cell_key -> judge_model``.

    Restricted to ``judge_query``/``judge_verdict`` events (the only roles the judge itself
    serves within a cell -- see :data:`_JUDGE_SERVED_CALL_ROLES`): a cell's query_checker and
    oracle_verification calls share the same ``cell_key`` but are served by different, fixed
    models, so including them would make one cell resolve to multiple "judges". The first
    judge-served event seen for a key fixes it; a later one that disagrees means the ledger
    itself is inconsistent, and is refused rather than silently overwritten.
    """
    mapping: dict[str, str] = {}
    with usage_ledger_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            metadata = event.get("metadata") or {}
            if metadata.get("call_role") not in _JUDGE_SERVED_CALL_ROLES:
                continue
            cell_key = metadata.get("cell_key")
            model = event.get("model")
            if not cell_key or not model:
                continue
            existing = mapping.get(cell_key)
            if existing is None:
                mapping[cell_key] = model
            elif existing != model:
                raise HistoryCapReplayError(
                    f"cell {cell_key!r} was served by two different models in the usage "
                    f"ledger: {existing!r} and {model!r}")
    return mapping


def iter_judge_query_responses(call_cache_path: Path):
    with call_cache_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("call_role") == JUDGE_QUERY_ROLE:
                yield row


def replay(*, call_cache_path: Path, usage_ledger_path: Path,
          role_limits: Mapping[str, Any]) -> dict[str, Any]:
    cell_judge_model = build_cell_judge_model_map(usage_ledger_path)
    model_role_limits = role_limits.get("model_role_limits") or {}

    checked = 0
    unresolved_cells: list[str] = []
    max_observed_by_class: dict[str, int] = {}
    violations: list[dict[str, Any]] = []
    cap_bytes_seen: dict[str, int] = {}

    for row in iter_judge_query_responses(call_cache_path):
        cell_key = row["cell_key"]
        model = cell_judge_model.get(cell_key)
        if model is None:
            unresolved_cells.append(cell_key)
            continue
        entry = model_role_limits.get(model, {}).get(JUDGE_QUERY_ROLE)
        if entry is None:
            raise HistoryCapReplayError(
                f"role-limits artifact has no ({model!r}, {JUDGE_QUERY_ROLE!r}) entry")
        effective_max_tokens = int(entry["effective_request_max_tokens"])
        try:
            cap_bytes = visible_history_cap_bytes(effective_max_tokens)
        except VisibleHistoryCapClassificationError as exc:
            raise HistoryCapReplayError(str(exc)) from exc
        class_label = _CLASS_LABELS.get(cap_bytes, str(cap_bytes))
        cap_bytes_seen[class_label] = cap_bytes

        observed_bytes = len(row["response"].encode("utf-8"))
        checked += 1
        if observed_bytes > max_observed_by_class.get(class_label, -1):
            max_observed_by_class[class_label] = observed_bytes
        if observed_bytes > cap_bytes:
            violations.append({
                "cell_key": cell_key, "slot": row.get("slot"), "attempt": row.get("attempt"),
                "model": model, "class": class_label, "cap_bytes": cap_bytes,
                "observed_bytes": observed_bytes,
            })

    return {
        "responses_checked": checked,
        "unresolved_cells": unresolved_cells,
        "cap_bytes_by_class": cap_bytes_seen,
        "max_observed_bytes_by_class": max_observed_by_class,
        "violations": violations,
    }


def build_report(*, call_cache_path: Path, usage_ledger_path: Path,
                 role_limits: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    result = replay(call_cache_path=call_cache_path, usage_ledger_path=usage_ledger_path,
                    role_limits=role_limits)
    return {
        "generated_at": generated_at,
        "schema_version": "phase3_history_cap_replay_v1",
        "call_cache_path": str(call_cache_path).replace("\\", "/"),
        "usage_ledger_path": str(usage_ledger_path).replace("\\", "/"),
        "cap_classification": {
            "module": "rejudge.judge_loop", "function": "visible_history_cap_bytes",
        },
        **result,
    }


def _canonical_bytes(report: Mapping[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--call-cache", required=True, help="the live, READ-ONLY call cache")
    parser.add_argument("--usage-ledger", required=True, help="the live, READ-ONLY usage ledger")
    parser.add_argument("--role-limits",
                        default=str(REPO_ROOT / "rejudge"
                                   / "phase3_role_limits_v1_2026-08-18.json"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)

    try:
        role_limits = json.loads(Path(args.role_limits).read_text(encoding="utf-8"))
        generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
        report = build_report(
            call_cache_path=Path(args.call_cache), usage_ledger_path=Path(args.usage_ledger),
            role_limits=role_limits, generated_at=generated_at)
    except (HistoryCapReplayError, KeyError, ValueError, OSError) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(_canonical_bytes(report))

    print(f"responses_checked={report['responses_checked']} "
         f"violations={len(report['violations'])} "
         f"unresolved_cells={len(report['unresolved_cells'])} "
         f"max_observed_bytes_by_class={report['max_observed_bytes_by_class']}")
    return 0 if not report["violations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
