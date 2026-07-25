"""The re-judge core loop: a port of the pilot judge flow with per-arm hooks."""
from __future__ import annotations

from rejudge import composer, oracle_channel, records
from rejudge.config import ARMS, JUDGE_MODEL, PLACEBO_TEXT, ArmSpec, clean_query_phase_prompt, \
    judgment_seed, position_for
from rejudge.parsers import parse_both


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


def run_judgment(transcript: dict, world_document: str, arm: ArmSpec, budget: int,
                 replicate: int, client, protocol: dict,
                 judge_model: str = JUDGE_MODEL, *,
                 position_override: bool | None = None,
                 query_template_override: str | None = None,
                 query_gate=None) -> dict:
    """Run one judgment cell.

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
    """
    qid, tidx = transcript["question_id"], transcript["transcript_index"]
    cell_key = f"{arm.name}|{qid}|{tidx}|{budget}|{replicate}"
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
    seed = judgment_seed(qid, tidx, judge_model, budget, arm.name, replicate)

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
                    exchanges.append({
                        "raw_query_response": raw_q, "extracted_claim": claim,
                        "well_formed_claim": well_formed, "oracle_prompt": None,
                        "raw_oracle_reply": None, "normalized": None,
                        "placebo": arm.placebo, "blocked": True,
                        "blocked_feedback": feedback})
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
