"""Offline, deterministic worst-case context precheck: produces a context-exclusion blocklist.

The 2026-08-19 canary halt (``ContextGuardError`` on ``SEL-010 x Qwen2.5-7B x sequential_b8``)
is not a bug: the judge context for a long, uncapped-transcript b8 cell genuinely exceeds
Qwen2.5-7B-Instruct-Turbo's and google/gemma-3n-E4B-it's 32,768-token ceilings, and truncation
is a zero-tolerance gate (``rejudge.api_client.RejudgeClient._resolve_context_ceiling`` /
``ContextGuardError`` refuse rather than truncate). The fix is a DETERMINISTIC EX-ANTE
EXCLUSION -- decided once, offline, before any live run -- never mid-run discretion.

**Reuses the live guard's own arithmetic, never a separate approximation.** Amendment 4
(2026-08-19, rejudge/phase3_amendment4_context_guard_2026-08-19.json) replaced the guard
comparison's estimator: it is now :func:`rejudge.api_client.estimate_context_tokens`, imported
and called unmodified -- ``E_prompt = ceil(prompt_utf8_bytes / 3) + 512`` and
``E_total = E_prompt + effective_request_max_tokens`` -- the SAME function
``RejudgeClient.complete`` calls and compares its ``E_total`` against
``model_context_limits[model]`` to decide whether to raise (``_estimate_usage`` continues to
drive spend/reservations only, and is untouched by this amendment). This module's whole job is
to assemble the WORST-CASE ``messages`` list a judgment cell's FINAL (verdict) call could ever
carry, then hand it to that exact function.

**The base (real transcript presentation) needs no estimation at all.** Phase 3 reuses
byte-identical phase-2 transcripts under the ``uncapped3`` debate protocol
(``rejudge.debate_gen.PROTOCOL_WORD_CAPS["uncapped3"] is None`` -- no word cap was ever
applied), so the transcript text a precheck cell will ACTUALLY see already exists, verbatim, in
the frozen transcript bundle. The presentation messages (system prompt + the composed
question/positions/debate-transcript user message) are built through the real composer path
(``rejudge.judge_loop._format_transcript`` + the frozen ``sequential_judge_presentation``
template, exactly as ``judge_loop.run_judgment`` itself builds them) -- an EXACT value, not a
bound. Also load-bearing: the presentation template is identical across every condition (b0's
composition and every ``sequential_bN`` composition both name
``"presentation": "sequential_judge_presentation"``), and swapping which side is "correct" only
reorders which of ``{correct_answer, wrong_answer}`` lands at position_a vs. position_b --
total presentation length is side-independent -- so this module computes the base ONCE per
``(debater_model, question_id, transcript_index)``, never once per cell.

**The query history is the genuinely worst-case part, because it is not yet real text.** No
future judge_query response, oracle reply, or checker/reviewer verdict exists yet to measure.
This module instead assembles the LONGEST plausible history the frozen machinery could ever
produce for a given ``(condition, judge_model)`` pair -- independent of any specific question,
so it too is computed once per pair, not once per cell:

- every query slot is assumed to use its one free retry
  (``rejudge.phase2_query_gate``'s frozen one-attempt-then-one-retry contract, mirrored here as
  ``MAX_ATTEMPTS_PER_SLOT``) before being BLOCKED with the longer of the two fixed terminal
  feedbacks -- the frozen no-query-transition payload (147 bytes) is longer than the longest
  possible oracle reply (``rejudge.oracle_channel.normalize_strict``'s fixed vocabulary is
  exactly ``{"YES", "NO", "NOT ADDRESSED"}``; even ``"Oracle result: NOT ADDRESSED"`` is
  shorter), matching ``judge_loop._result_as_shown``'s blocked branch. This maximizes both the
  message COUNT per slot (5 instead of 3) and the per-message CONTENT length;
- every judge_query response (both the live message content and its extracted-claim echo
  inside every later round's growing ``previous_queries``/``query_results`` blocks -- a
  genuinely quadratic-in-``query_budget`` cost, which is exactly why b8 is the condition that
  overflows) is modelled as a placeholder string of length EXACTLY the MECHANICALLY ENFORCED
  visible-history byte cap for that model's judge_query class
  (:func:`rejudge.judge_loop.visible_history_cap_bytes`; amendment 4, package item 2), in
  single-byte ASCII characters. The Codex consult specifically rejected the prior
  ``"X" * judge_query_role_max_tokens`` construction: a requested ``max_tokens`` is an
  output-token budget, not a byte or token bound on what actually enters history, and the live
  guard now refuses (never truncates) any response exceeding the cap before it ever enters
  history -- so the worst case a completed cell's history can ever actually contain is the cap
  itself, for BOTH the retried (attempt 1) and blocked (attempt 2) slot.

**A cell is excluded when worst_case_tokens >= ceiling** -- not only when strictly greater, the
live guard's own threshold. This precheck's own construction is itself a bound, not an exact
simulation of real generated text, so exclusion uses the more conservative ``>=`` to absorb
that residual approximation; "truncation is a zero-tolerance gate" argues for over-excluding,
never under-excluding.

Deterministic: given the same protocol, roster, role-limits, bundle and transcript bundle, and
the same ``--generated-at`` (or a caller-supplied ``generated_at`` to :func:`build_report`), the
emitted JSON is byte-identical. No network, no provider client, no randomness.

CLI: ``--protocol``, ``--manifest`` (derives roster + role-limits path) OR explicit
``--roster``/``--role-limits``, ``--bundle-dir`` (holding the frozen transcript bundle(s)),
``--scope canary|main``, ``--out``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_manifest, phase3_plan, phase3_runner  # noqa: E402
from rejudge.api_client import estimate_context_tokens  # noqa: E402
from rejudge.judge_loop import _format_previous  # noqa: E402
from rejudge.judge_loop import _format_transcript as format_transcript  # noqa: E402
from rejudge.judge_loop import visible_history_cap_bytes  # noqa: E402
from rejudge.phase2_canary_compose import load_no_query_payload  # noqa: E402

# Amendment 4 (2026-08-19): the precheck mirrors the LIVE guard's own arithmetic, which as of
# this amendment is estimate_context_tokens (the context-only estimator), never _estimate_usage
# (which continues to drive spend/reservations only -- see api_client.py's module note).
ESTIMATOR_PROVENANCE = {"module": "rejudge.api_client", "function": "estimate_context_tokens"}

# rejudge.phase2_query_gate's frozen one-attempt-then-one-free-retry contract
# (judge_loop.run_judgment's own _MAX_ATTEMPTS_PER_SLOT): worst case always spends it.
MAX_ATTEMPTS_PER_SLOT = 2

PRESENTATION_TEMPLATE_NAME = "sequential_judge_presentation"
QUERY_TEMPLATE_NAME = "sequential_judge_query"
VERDICT_TEMPLATE_NAME = "sequential_judge_verdict"
REJECTION_TEMPLATE_NAME = "sequential_judge_rejection"


class ContextPrecheckError(ValueError):
    """Raised when the precheck cannot proceed. Always fails closed."""


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- role-limit / ceiling lookups, all reading the SAME bound role-limits artifact ----------


def _role_limit(role_limits: Mapping[str, Any], judge_model: str, role: str) -> int:
    entry = (role_limits.get("model_role_limits") or {}).get(judge_model, {}).get(role)
    if entry is None:
        raise ContextPrecheckError(
            f"role-limits artifact has no {role!r} entry for judge {judge_model!r}")
    return int(entry["effective_request_max_tokens"])


def judge_query_max_tokens(role_limits: Mapping[str, Any], judge_model: str) -> int:
    return _role_limit(role_limits, judge_model, "judge_query")


def judge_verdict_max_tokens(role_limits: Mapping[str, Any], judge_model: str) -> int:
    return _role_limit(role_limits, judge_model, "judge_verdict")


def context_ceiling(role_limits: Mapping[str, Any], judge_model: str) -> int:
    entry = (role_limits.get("context_ceilings") or {}).get(judge_model)
    if entry is None:
        raise ContextPrecheckError(f"role-limits artifact has no context ceiling for {judge_model!r}")
    ceiling = entry.get("context_length_tokens")
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling <= 0:
        raise ContextPrecheckError(f"invalid context ceiling for {judge_model!r}: {ceiling!r}")
    return int(ceiling)


# --- worst-case message construction ---------------------------------------------------------


def base_presentation_messages(transcript: Mapping[str, Any],
                               presentation_template: Mapping[str, str]) -> list[dict[str, str]]:
    """The EXACT presentation messages (system + first user turn) for a real transcript.

    Side-independent (swapping which candidate is "correct" only reorders position_a/position_b,
    never changes total length), so ``pos_a_correct`` is fixed arbitrarily to ``True``.
    """
    position_a, position_b, debate_text = format_transcript(transcript, True)
    return [
        {"role": "system", "content": presentation_template["system_prompt"]},
        {"role": "user", "content": presentation_template["user_prompt_template"].format(
            question=transcript["question"], position_a=position_a,
            position_b=position_b, debate_transcript=debate_text)},
    ]


def worst_case_query_history_and_verdict_messages(
    *, query_budget: int, judge_query_role_max_tokens: int,
    query_template: str, verdict_template: str,
    rejection_payload: str, no_query_payload: str,
) -> list[dict[str, str]]:
    """The longest plausible query-loop-plus-verdict tail for one ``(condition, judge)`` pair.

    Mirrors ``rejudge.judge_loop.run_judgment``'s query loop and verdict construction message
    for message, substituting worst-case placeholders only where the real content does not yet
    exist (see the module docstring). Independent of any specific question or transcript.

    Amendment 4 (2026-08-19), package items 2 and 3: the placeholder for a query-loop response
    is now bounded by the MECHANICALLY ENFORCED visible-history byte cap
    (``rejudge.judge_loop.visible_history_cap_bytes``), not by
    ``judge_query_role_max_tokens`` characters. The Codex consult specifically rejected the
    prior ``"X" * max_tokens`` construction (rejudge/phase3_codex_context_guard_consult_2026-08-
    19.md, "3."): a requested ``max_tokens`` is an output-token budget, not a byte or token bound
    on visible history, and the live guard now refuses any response that would exceed the cap
    before it ever enters history -- so the worst case a completed cell's history can ever
    actually contain is the cap itself, for BOTH the retried (attempt 1) and blocked (attempt 2)
    slot.
    """
    messages: list[dict[str, str]] = []
    feedback_pairs: list[tuple[str, str]] = []
    claim_placeholder = "X" * visible_history_cap_bytes(judge_query_role_max_tokens)
    for query_num in range(query_budget):
        remaining = query_budget - query_num
        messages.append({"role": "user", "content": query_template.format(
            remaining_budget=remaining, total_budget=query_budget,
            previous_queries=_format_previous(feedback_pairs))})
        # Attempt 1: the free retry (never consumes the slot, so the loop asks again).
        messages.append({"role": "assistant", "content": claim_placeholder})
        messages.append({"role": "user", "content": rejection_payload})
        # Attempt 2 (MAX_ATTEMPTS_PER_SLOT): terminal -- BLOCKED with the longer fixed
        # feedback, maximizing this slot's contribution.
        messages.append({"role": "assistant", "content": claim_placeholder})
        messages.append({"role": "user", "content": no_query_payload})
        feedback_pairs.append((claim_placeholder, no_query_payload))
    # judge_loop.run_judgment lines ~221-227: query_results is "" (not "No queries submitted
    # yet.") when nothing was exchanged -- a DIFFERENT empty-case string than
    # _format_previous's own, so it is reproduced literally rather than reused for this part.
    query_results = ""
    if feedback_pairs:
        query_results = "VERIFICATION RESULTS:\n\n" + "\n\n".join(
            f"Query {i}: {claim}\nResult: {result}"
            for i, (claim, result) in enumerate(feedback_pairs, 1))
    messages.append({"role": "user",
                     "content": verdict_template.format(query_results=query_results)})
    return messages


# --- plan / transcript loading ----------------------------------------------------------------


def load_transcript_index(transcript_bundle: Mapping[str, Any]
                          ) -> dict[tuple[str, str, int], dict[str, Any]]:
    return {
        (str(entry["debater_model"]), str(entry["question_id"]), int(entry["transcript_index"])):
            entry["transcript_payload"]
        for entry in transcript_bundle["transcripts"]
    }


def _judgment_kind_for(scope: str) -> str:
    return (phase3_plan.CANARY_JUDGMENT_KIND if scope == "canary"
           else phase3_plan.MAIN_JUDGMENT_KIND)


def enumerate_plan_cells(protocol: Mapping[str, Any], *, scope: str,
                         roster_judges: Sequence[str], project_root: str | Path,
                         ) -> list[dict[str, Any]]:
    root = Path(project_root)
    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    if scope == "canary":
        return phase3_plan.enumerate_canary_cells(protocol, roster_judges, held_out_ids)
    if scope == "main":
        return phase3_plan.enumerate_cells(protocol, roster_judges, main_ids)
    raise ContextPrecheckError(f"unknown scope {scope!r}")


# --- the precheck itself -----------------------------------------------------------------------


def compute_blocklist(*, protocol: Mapping[str, Any], bundle: Mapping[str, Any],
                      role_limits: Mapping[str, Any], plan_cells: Sequence[Mapping[str, Any]],
                      transcript_index: Mapping[tuple[str, str, int], Mapping[str, Any]],
                      scope: str) -> dict[str, Any]:
    """Return ``{"excluded": [...], "counts": {(judge, condition): n}, "ceilings_used": {...}}``.

    ``excluded`` is sorted by ``cell_key`` and ``counts`` by ``(judge, condition)`` for
    deterministic emission. Refuses (fails closed) if a cell names a condition the protocol
    does not define, or a plan cell has no matching transcript bundle record -- both mean the
    plan and its inputs have drifted, and a precheck computed against drifted inputs is worse
    than no precheck at all.
    """
    conditions = phase3_runner._condition_lookup(protocol)
    templates = bundle["templates"]
    presentation_template = templates[PRESENTATION_TEMPLATE_NAME]
    query_template = templates[QUERY_TEMPLATE_NAME]["user_prompt_template"]
    verdict_template = templates[VERDICT_TEMPLATE_NAME]["user_prompt_template"]
    rejection_payload = templates[REJECTION_TEMPLATE_NAME]["payload"]
    no_query_payload = load_no_query_payload(REPO_ROOT)

    judgment_kind = _judgment_kind_for(scope)

    history_cache: dict[tuple[str, str], tuple[list[dict[str, str]], int]] = {}
    base_cache: dict[tuple[str, str, int], list[dict[str, str]]] = {}
    ceilings_used: dict[str, int] = {}

    excluded: list[dict[str, Any]] = []
    counts: dict[tuple[str, str], int] = {}

    for cell in plan_cells:
        if str(cell["kind"]) != judgment_kind:
            continue
        condition_id = str(cell["condition"])
        judge_model = str(cell["judge_model"])
        debater_model = str(cell["debater_model"])
        question_id = str(cell["question_id"])
        transcript_idx = cell.get("transcript_index")

        protocol_condition = conditions.get(condition_id)
        if protocol_condition is None:
            raise ContextPrecheckError(
                f"cell {cell['cell_key']} names condition {condition_id!r}, which is not in "
                "the frozen protocol's debate_grid.conditions")
        query_budget = int(protocol_condition["query_budget"])

        history_key = (condition_id, judge_model)
        if history_key not in history_cache:
            jq_max = judge_query_max_tokens(role_limits, judge_model)
            history_messages = worst_case_query_history_and_verdict_messages(
                query_budget=query_budget, judge_query_role_max_tokens=jq_max,
                query_template=query_template, verdict_template=verdict_template,
                rejection_payload=rejection_payload, no_query_payload=no_query_payload)
            verdict_max = judge_verdict_max_tokens(role_limits, judge_model)
            history_cache[history_key] = (history_messages, verdict_max)
        history_messages, verdict_max = history_cache[history_key]

        base_key = (debater_model, question_id, int(transcript_idx))
        if base_key not in base_cache:
            transcript = transcript_index.get(base_key)
            if transcript is None:
                raise ContextPrecheckError(
                    f"cell {cell['cell_key']} needs transcript {base_key!r}, which is not in "
                    "the frozen transcript bundle")
            base_cache[base_key] = base_presentation_messages(transcript, presentation_template)
        base_messages = base_cache[base_key]

        _, worst_case_tokens = estimate_context_tokens(
            base_messages + history_messages, verdict_max)

        if judge_model not in ceilings_used:
            ceilings_used[judge_model] = context_ceiling(role_limits, judge_model)
        ceiling = ceilings_used[judge_model]

        if worst_case_tokens >= ceiling:
            excluded.append({
                "cell_key": str(cell["cell_key"]), "judge_model": judge_model,
                "question_id": question_id, "debater_model": debater_model,
                "transcript_index": transcript_idx, "condition": condition_id,
                "worst_case_tokens": worst_case_tokens, "ceiling": ceiling,
            })
            key = (judge_model, condition_id)
            counts[key] = counts.get(key, 0) + 1

    excluded.sort(key=lambda entry: entry["cell_key"])
    counts_sorted = [{"judge_model": judge, "condition": condition, "count": n}
                     for (judge, condition), n in sorted(counts.items())]
    return {"excluded": excluded, "counts": counts_sorted, "ceilings_used": dict(sorted(
        ceilings_used.items()))}


def build_report(*, protocol: Mapping[str, Any], bundle: Mapping[str, Any],
                 role_limits: Mapping[str, Any], plan_cells: Sequence[Mapping[str, Any]],
                 transcript_index: Mapping[tuple[str, str, int], Mapping[str, Any]],
                 scope: str, generated_at: str) -> dict[str, Any]:
    """The full emitted document. ``generated_at`` is caller-supplied (never internally
    generated with ``datetime.now()``), matching ``rejudge.phase3_manifest.build_manifest``'s
    own ``recorded_at_utc`` convention -- so the SCIENTIFIC content stays byte-identical for
    byte-identical inputs across repeated CLI invocations, and reproducibility never depends on
    the caller happening to also supply the same wall-clock stamp.
    """
    result = compute_blocklist(
        protocol=protocol, bundle=bundle, role_limits=role_limits, plan_cells=plan_cells,
        transcript_index=transcript_index, scope=scope)
    return {
        "generated_at": generated_at,
        "scope": scope,
        "cell_key_namespace": str(protocol["cell_key_namespace"]),
        "estimator_provenance": dict(ESTIMATOR_PROVENANCE),
        "ceilings_used": result["ceilings_used"],
        "excluded": result["excluded"],
        "counts_by_judge_and_condition": result["counts"],
        "excluded_count": len(result["excluded"]),
    }


def _canonical_bytes(report: Mapping[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")


# --- CLI -----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--protocol", default=str(phase3_plan.DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--project-root", default=str(REPO_ROOT))
    parser.add_argument("--manifest", default=None,
                        help="derive roster and role-limits path from this manifest")
    parser.add_argument("--roster", nargs="+", default=None,
                        help="explicit roster judges; overrides --manifest's roster")
    parser.add_argument("--role-limits", default=None,
                        help="explicit role-limits artifact path; overrides the manifest-bound one")
    parser.add_argument("--bundle-dir", required=True,
                        help="directory holding the frozen transcript bundle(s), read-only")
    parser.add_argument("--scope", choices=("canary", "main"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--generated-at", default=None,
                        help="ISO-8601 UTC timestamp to record; defaults to now (breaks byte-"
                             "identical reproduction across invocations unless pinned)")
    args = parser.parse_args(argv)

    try:
        root = Path(args.project_root)
        protocol = phase3_plan.load_protocol(args.protocol)

        if args.roster is not None:
            roster_judges = list(args.roster)
        elif args.manifest is not None:
            manifest = _load_json(args.manifest)
            roster_judges = list(manifest["roster"]["judges"])
        else:
            raise ContextPrecheckError("one of --manifest or --roster is required")

        if args.role_limits is not None:
            role_limits_path = Path(args.role_limits)
        elif args.manifest is not None:
            manifest = _load_json(args.manifest)
            role_limits_path = root / str(manifest["frozen_inputs"]["role_limits_tracked_path"])
        else:
            role_limits_path = root / phase3_manifest.ROLE_LIMITS_RELATIVE_PATH
        role_limits = _load_json(role_limits_path)

        bundle = _load_json(root / phase3_manifest.PHASE2_PROMPT_BUNDLE_RELATIVE_PATH)

        bundle_dir = Path(args.bundle_dir)
        bundle_relative = (phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH
                           if args.scope == "canary"
                           else phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH)
        transcript_bundle = _load_json(bundle_dir / bundle_relative.name)
        transcript_index = load_transcript_index(transcript_bundle)

        plan_cells = enumerate_plan_cells(
            protocol, scope=args.scope, roster_judges=roster_judges, project_root=root)

        generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
        report = build_report(
            protocol=protocol, bundle=bundle, role_limits=role_limits, plan_cells=plan_cells,
            transcript_index=transcript_index, scope=args.scope, generated_at=generated_at)
    except (ContextPrecheckError, phase3_plan.ProtocolValidationError, KeyError, ValueError) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        handle.write(_canonical_bytes(report))
        handle.flush()
        os.fsync(handle.fileno())

    print(f"{args.scope}: {report['excluded_count']} cell(s) excluded, written to {out_path}")
    for row in report["counts_by_judge_and_condition"]:
        print(f"  {row['judge_model']} / {row['condition']}: {row['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
