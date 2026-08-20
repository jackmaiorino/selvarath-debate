"""The re-judge core loop: a port of the pilot judge flow with per-arm hooks."""
from __future__ import annotations

from typing import Any, Mapping

from rejudge import composer, oracle_channel, records
from rejudge.config import ARMS, JUDGE_MODEL, PLACEBO_TEXT, ArmSpec, clean_query_phase_prompt, \
    judgment_seed, position_for
from rejudge.parsers import parse_both
from rejudge.phase2_role_limits import BASE_ROLE_MAX_TOKENS as _BASE_ROLE_MAX_TOKENS
from rejudge.phase2_role_limits import REASONING_FLOOR_MAX_TOKENS as _REASONING_FLOOR_MAX_TOKENS


def _format_transcript(tr: dict, position_a_is_correct: bool):
    """Port of judge.py:_format_transcript for raw-dict transcripts."""
    if position_a_is_correct:
        position_a, position_b = tr["correct_answer"], tr["wrong_answer"]
    else:
        position_a, position_b = tr["wrong_answer"], tr["correct_answer"]
    turns = []
    for i, turn in enumerate(tr["debate_transcript"]):
        if position_a_is_correct:
            label = "Debater A" if turn["speaker"] == "honest" else "Debater B"
        else:
            label = "Debater B" if turn["speaker"] == "honest" else "Debater A"
        turns.append(f"Turn {i + 1} ({label}):\n{turn['text']}")
    return position_a, position_b, "\n\n".join(turns)


def _result_as_shown(exchange: dict) -> str:
    """The result text the judge saw for one exchange, for the verdict block.

    A blocked slot shows the frozen no-query transition in both clean and placebo mode:
    the gate, not the mode, decided that nothing was dispatched.
    """
    if exchange.get("blocked"):
        return exchange["blocked_feedback"]
    return PLACEBO_TEXT if exchange["placebo"] else exchange["normalized"]


def _format_previous(exchange_feedback: list[tuple[str, str]]) -> str:
    if not exchange_feedback:
        return "No queries submitted yet."
    return "\n\n".join(f"Query {i}: {q}\nResult: {r}"
                       for i, (q, r) in enumerate(exchange_feedback, 1))


# Retry attempts within one budget slot need their own seeds. Attempt 1 keeps the Stage-1
# expression exactly (seed + query_num), so the legacy path is byte-identical; only a gated
# retry ever offsets, and it wraps to stay inside the 32-bit seed range the manifests require.
_RETRY_SEED_STRIDE = 1_000_000
_SEED_MODULUS = 2 ** 32

# The frozen contract grants exactly one free retry per slot, so a slot can hold at most two
# attempts. A gate asking for a third has violated the policy; spinning on it would burn paid
# judge calls until the spend cap tripped, so refuse instead.
_MAX_ATTEMPTS_PER_SLOT = 2


class QueryRetryPolicyError(RuntimeError):
    """Raised when a query gate asks for more attempts than the frozen contract allows."""


# --- Amendment 4 (2026-08-19), package item 2: mechanically enforced visible-history byte caps -
#
# rejudge/phase3_amendment4_context_guard_2026-08-19.json, per the Codex consult
# (rejudge/phase3_codex_context_guard_consult_2026-08-19.md, "3."): a judge_query response's
# hidden reasoning never re-enters later messages, but its VISIBLE text does, quadratically, as
# both a raw history message and a quoted echo in every later round's growing
# previous_queries/query_results blocks -- with no length check anywhere in the semantic screen.
# A fixed ledger prefix held visible query responses as large as 2,204 UTF-8 bytes (gpt-oss) and
# 2,839 bytes (gemma-4); these caps are at least 2x those observed per-class maxima, rounded up.
JUDGE_QUERY_ROLE = "judge_query"
_JUDGE_QUERY_BASE_MAX_TOKENS = _BASE_ROLE_MAX_TOKENS[JUDGE_QUERY_ROLE]

VISIBLE_HISTORY_CAP_BASE_BYTES = 1024
VISIBLE_HISTORY_CAP_REASONING_BYTES = 6144

# Shown to the judge model in place of the raw text when a query-loop response is retried or
# blocked purely for exceeding its visible-history byte cap -- never inserted into `messages`,
# only recorded in the audit-trail `exchanges` entry for a BLOCKED slot (a retried slot records
# nothing; see run_judgment's query loop, which reuses the existing retry/block transition
# unchanged).
_OVER_CAP_CLAIM_PLACEHOLDER = "[oracle-query response omitted: exceeded the visible-history byte cap]"


class VisibleHistoryCapClassificationError(ValueError):
    """The bound role-limits artifact's judge_query effective_request_max_tokens for a model is
    neither the frozen base value nor the frozen reasoning floor.

    Refuses rather than guessing a visible-history byte cap for an unclassified role-limit
    value -- truncation is a zero-tolerance gate, so an unrecognized value must halt, not fall
    back to either cap.
    """


def visible_history_cap_bytes(judge_query_effective_max_tokens: int) -> int:
    """The visible-history byte cap for a judge_query ``effective_request_max_tokens`` value.

    Purely value-based, never a model name or a hard-coded model set: the classification is
    entirely a function of the (already role-limits-artifact-resolved) effective value for
    ``(judge_model, "judge_query")``, so which models are "reasoning" is decided in exactly one
    place -- the bound role-limits artifact -- never duplicated here.
    """
    if judge_query_effective_max_tokens == _JUDGE_QUERY_BASE_MAX_TOKENS:
        return VISIBLE_HISTORY_CAP_BASE_BYTES
    if judge_query_effective_max_tokens == _REASONING_FLOOR_MAX_TOKENS:
        return VISIBLE_HISTORY_CAP_REASONING_BYTES
    raise VisibleHistoryCapClassificationError(
        f"judge_query effective_request_max_tokens {judge_query_effective_max_tokens!r} is "
        f"neither the frozen base ({_JUDGE_QUERY_BASE_MAX_TOKENS}) nor the frozen reasoning "
        f"floor ({_REASONING_FLOOR_MAX_TOKENS}); refusing to guess a visible-history byte cap")


def judge_query_effective_max_tokens(role_limits: Mapping[str, Any], judge_model: str) -> int:
    """Read ``(judge_model, "judge_query").effective_request_max_tokens`` off the bound
    role-limits artifact. Fails closed if the artifact has no entry for this judge."""
    entry = (role_limits.get("model_role_limits") or {}).get(judge_model, {}).get(
        JUDGE_QUERY_ROLE)
    if entry is None:
        raise VisibleHistoryCapClassificationError(
            f"role-limits artifact has no {JUDGE_QUERY_ROLE!r} entry for judge {judge_model!r}")
    return int(entry["effective_request_max_tokens"])


def run_judgment(transcript: dict, world_document: str, arm: ArmSpec, budget: int,
                 replicate: int, client, protocol: dict,
                 judge_model: str = JUDGE_MODEL, *,
                 position_override: bool | None = None,
                 query_template_override: str | None = None,
                 cell_key_override: str | None = None,
                 query_gate=None,
                 debater_model=None,
                 namespace=None,
                 role_limits: Mapping[str, Any] | None = None,
                 rejection_payload: str | None = None,
                 no_query_payload: str | None = None) -> dict:
    """Run one judgment cell.

    ``debater_model`` and ``namespace`` are optional and keyword-only, forwarded to
    :func:`rejudge.config.judgment_seed` unchanged. Left at their ``None`` default (every
    phase-2 call site), the computed seed is byte-for-byte identical to phase-2 behavior.
    Phase 3 supplies them per ``decisions.execution_semantics.seed_policy``.

    ``query_template_override`` supplies an already-final query-phase template, bypassing the
    Stage-1 rewrite of the pilot phrasing line. Phase-2 templates come from the frozen prompt
    bundle in final form and contain no pilot anchor line to rewrite.

    ``query_gate`` is called with ``(raw_query, claim, slot, attempt)`` for every proposed
    query and returns ``(action, feedback)``:

    - ``("allow", None)``  dispatch to the oracle, or to the placebo responder
    - ``("retry", text)``  show ``text``, do not consume the slot, re-ask within it
    - ``("block", text)``  show ``text``, consume the slot, dispatch nothing

    The gate owns the policy; this loop only executes the action it returns. With both
    parameters omitted the loop is the unchanged Stage-1 path.

    ``role_limits`` (amendment 4, package item 2), when supplied, activates the mechanically
    enforced visible-history byte cap on judge_query responses -- see
    :func:`visible_history_cap_bytes` -- checked BEFORE a response ever enters ``messages``,
    never by truncation. An over-cap response is routed through the SAME retry-then-block
    transition an existing mechanical/checker rejection already uses, reusing ``rejection_payload``
    /``no_query_payload`` (both required together with ``role_limits``) rather than inventing a
    new one. Left at its ``None`` default (every phase-2 call site), this check never runs and
    the loop is byte-identical to before amendment 4.
    """
    qid, tidx = transcript["question_id"], transcript["transcript_index"]
    # The Stage-1 key identifies a cell by arm, question, transcript, budget and replicate,
    # and by neither model. That is unambiguous in Stage 1, where one judge runs at a time,
    # but in phase 2 the same tuple describes four judges against two debaters, so every one
    # of them would share an identity. Callers that have a genuinely unique key pass it.
    cell_key = cell_key_override or f"{arm.name}|{qid}|{tidx}|{budget}|{replicate}"
    usage_base = {
        "stage": "judgment",
        "cell_key": cell_key,
        "arm": arm.name,
        "question_id": qid,
        "transcript_index": tidx,
        "budget": budget,
        "replicate": replicate,
        "judge_model": judge_model,
    }
    pos_a_correct = (position_for(arm, qid, tidx, judge_model, budget)
                     if position_override is None else position_override)
    seed = judgment_seed(qid, tidx, judge_model, budget, arm.name, replicate,
                        debater_model=debater_model, namespace=namespace)

    judge_cfg = protocol["judge"]
    oracle_cfg = protocol["oracle"]
    oracle_model = protocol["protocol"]["models"]["oracle"]
    t_judge = protocol["protocol"]["temperature"]["judge"]
    t_oracle = protocol["protocol"]["temperature"]["oracle"]

    # Resolved lazily: a budget-0 cell runs no query loop, and a phase-2 budget-0 condition
    # legitimately composes no query template at all, so computing one eagerly would crash on
    # a cell that never asks a question.
    def _query_template() -> str:
        if query_template_override is not None:
            return query_template_override
        return (judge_cfg["query_phase_prompt"] if arm.composer == "pilot"
                else clean_query_phase_prompt(judge_cfg["query_phase_prompt"]))
    is_done = (oracle_channel.is_done_pilot if arm.done_detector == "pilot"
               else oracle_channel.is_done_robust)
    normalize = (oracle_channel.normalize_pilot if arm.oracle_normalizer == "pilot"
                 else oracle_channel.normalize_strict)

    position_a, position_b, debate_text = _format_transcript(transcript, pos_a_correct)
    messages = [
        {"role": "system", "content": judge_cfg["system_prompt"]},
        {"role": "user", "content": judge_cfg["user_prompt_template"].format(
            question=transcript["question"], position_a=position_a,
            position_b=position_b, debate_transcript=debate_text)},
    ]

    exchanges = []
    feedback_pairs = []          # (claim-as-shown, result-as-shown) for the verdict block
    if budget > 0:
        judge_is_done = False
        judge_query_cap_bytes = None
        if role_limits is not None:
            judge_query_cap_bytes = visible_history_cap_bytes(
                judge_query_effective_max_tokens(role_limits, judge_model))
            if rejection_payload is None or no_query_payload is None:
                raise ValueError(
                    "role_limits was supplied but rejection_payload/no_query_payload were not; "
                    "the visible-history over-cap transition reuses those exact texts rather "
                    "than inventing new ones")
        for query_num in range(budget):
            if judge_is_done:
                break
            remaining = budget - query_num
            attempt = 1
            while True:
                if attempt == 1:
                    messages.append({"role": "user", "content": _query_template().format(
                        remaining_budget=remaining, total_budget=budget,
                        previous_queries=_format_previous(feedback_pairs))})
                query_seed = seed + query_num
                if attempt > 1:
                    query_seed = (query_seed
                                  + (attempt - 1) * _RETRY_SEED_STRIDE) % _SEED_MODULUS
                query_metadata = {**usage_base, "call_role": "judge_query",
                                  "query_index": query_num}
                if query_gate is not None:
                    query_metadata["attempt"] = attempt
                raw_q = client.complete(messages, judge_model, t_judge,
                                        query_seed, 256, kind="query",
                                        request_metadata=query_metadata)

                # Amendment 4, package item 2: checked BEFORE history insertion. An over-cap
                # response never enters `messages` -- truncated or otherwise. The TRANSITION
                # itself, though, is never synthesized here: when a query_gate is present (every
                # production canary/main call site), the gate owns all attempt/slot state and
                # the "every raw query gets a reviewer decision" invariant, so an over-cap
                # response is submitted to it exactly like any other candidate query and the
                # gate classifies it itself (Phase2QueryGate's own max_response_bytes check,
                # ahead of query_screen and ahead of the -- possibly billed -- checker). Bypassing
                # the gate here previously desynced its internal attempt counter from this loop's
                # local one (a reproduced QueryRetryPolicyError) and silently skipped the
                # reviewer-decision requirement for the discarded payload. Only when NO gate is
                # configured at all (no external state to desync) does this loop fall back to
                # synthesizing the identical retry-then-block transition itself.
                over_cap = (judge_query_cap_bytes is not None
                           and len(raw_q.encode("utf-8")) > judge_query_cap_bytes)
                if over_cap:
                    claim, well_formed = _OVER_CAP_CLAIM_PLACEHOLDER, None
                    if query_gate is not None:
                        action, feedback = query_gate(raw_q, claim, query_num + 1, attempt)
                    else:
                        action = "retry" if attempt < _MAX_ATTEMPTS_PER_SLOT else "block"
                        feedback = rejection_payload if action == "retry" else no_query_payload
                else:
                    messages.append({"role": "assistant", "content": raw_q})
                    if is_done(raw_q):
                        judge_is_done = True
                        break
                    if arm.composer == "pilot":
                        claim, well_formed = composer.pilot_extract_claim(raw_q), None
                    else:
                        claim, well_formed = composer.clean_extract_claim(raw_q)

                    action, feedback = ("allow", None)
                    if query_gate is not None:
                        action, feedback = query_gate(raw_q, claim, query_num + 1, attempt)
                if action == "retry":
                    if attempt >= _MAX_ATTEMPTS_PER_SLOT:
                        raise QueryRetryPolicyError(
                            f"query gate asked for attempt {attempt + 1} in slot "
                            f"{query_num + 1}; the frozen contract allows "
                            f"{_MAX_ATTEMPTS_PER_SLOT}")
                    messages.append({"role": "user", "content": feedback})
                    attempt += 1
                    continue
                if action == "block":
                    messages.append({"role": "user", "content": feedback})
                    feedback_pairs.append((claim, feedback))
                    blocked_exchange = {
                        "raw_query_response": raw_q, "extracted_claim": claim,
                        "well_formed_claim": well_formed, "oracle_prompt": None,
                        "raw_oracle_reply": None, "normalized": None,
                        "placebo": arm.placebo, "blocked": True,
                        "blocked_feedback": feedback}
                    # Amendment 4 re-review fix (2026-08-19): this key is added ONLY when the
                    # phase-3 cap is actually active (role_limits supplied). The real phase-2
                    # caller (rejudge.phase2_canary_live's CanaryQueryGate construction site,
                    # and every other pre-amendment run_judgment caller) never passes
                    # role_limits, and must get the EXACT pre-amendment exchange dict shape
                    # and serialized bytes back -- an unconditional extra key here changed both
                    # even though its value was always False for them.
                    if role_limits is not None:
                        blocked_exchange["context_guard_over_cap"] = over_cap
                    exchanges.append(blocked_exchange)
                    break

                if arm.placebo:
                    oracle_prompt = raw_reply = normalized = None
                    shown = PLACEBO_TEXT
                    messages.append({"role": "user", "content": shown})
                else:
                    oracle_prompt = composer.compose_oracle_prompt(
                        oracle_cfg["user_prompt_template"], world_document, claim)
                    raw_reply = client.complete(
                        [{"role": "system", "content": oracle_cfg["system_prompt"]},
                         {"role": "user", "content": oracle_prompt}],
                        oracle_model, t_oracle, seed + 10000 + query_num, 32, kind="oracle",
                        request_metadata={
                            **usage_base, "call_role": "oracle_verification",
                            "query_index": query_num,
                        })
                    normalized = normalize(raw_reply)
                    shown = f"Oracle result: {normalized}"
                    messages.append({"role": "user", "content": shown})
                feedback_pairs.append((claim, shown if arm.placebo else normalized))
                exchanges.append({"raw_query_response": raw_q, "extracted_claim": claim,
                                  "well_formed_claim": well_formed,
                                  "oracle_prompt": oracle_prompt,
                                  "raw_oracle_reply": raw_reply, "normalized": normalized,
                                  "placebo": arm.placebo})
                break

    query_results = ""
    if exchanges:
        query_results = "VERIFICATION RESULTS:\n\n" + "\n\n".join(
            f"Query {i}: {e['extracted_claim']}\nResult: {_result_as_shown(e)}"
            for i, e in enumerate(exchanges, 1))
    messages.append({"role": "user",
                     "content": judge_cfg["verdict_prompt"].format(query_results=query_results)})
    raw_verdict = client.complete(messages, judge_model, t_judge, seed + 99999, 512,
                                  kind="verdict", request_metadata={
                                      **usage_base, "call_role": "judge_verdict",
                                  })

    return records.build_record(
        transcript=transcript, arm=arm, budget=budget, replicate=replicate,
        position_a_is_correct=pos_a_correct, exchanges=exchanges,
        raw_verdict_text=raw_verdict, parses=parse_both(raw_verdict),
        judge_messages=messages + [{"role": "assistant", "content": raw_verdict}],
        seed=seed, judge_model=judge_model, oracle_model=oracle_model,
        dry_run=getattr(client, "dry_run", False), queries_used=len(exchanges))
