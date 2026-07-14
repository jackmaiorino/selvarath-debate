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


def _format_previous(exchange_feedback: list[tuple[str, str]]) -> str:
    if not exchange_feedback:
        return "No queries submitted yet."
    return "\n\n".join(f"Query {i}: {q}\nResult: {r}"
                       for i, (q, r) in enumerate(exchange_feedback, 1))


def run_judgment(transcript: dict, world_document: str, arm: ArmSpec, budget: int,
                 replicate: int, client, protocol: dict,
                 judge_model: str = JUDGE_MODEL, *,
                 position_override: bool | None = None) -> dict:
    qid, tidx = transcript["question_id"], transcript["transcript_index"]
    pos_a_correct = (position_for(arm, qid, tidx, judge_model, budget)
                     if position_override is None else position_override)
    seed = judgment_seed(qid, tidx, judge_model, budget, arm.name, replicate)

    judge_cfg = protocol["judge"]
    oracle_cfg = protocol["oracle"]
    oracle_model = protocol["protocol"]["models"]["oracle"]
    t_judge = protocol["protocol"]["temperature"]["judge"]
    t_oracle = protocol["protocol"]["temperature"]["oracle"]

    query_template = (judge_cfg["query_phase_prompt"] if arm.composer == "pilot"
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
        for query_num in range(budget):
            remaining = budget - query_num
            messages.append({"role": "user", "content": query_template.format(
                remaining_budget=remaining, total_budget=budget,
                previous_queries=_format_previous(feedback_pairs))})
            raw_q = client.complete(messages, judge_model, t_judge,
                                    seed + query_num, 256, kind="query")
            messages.append({"role": "assistant", "content": raw_q})
            if is_done(raw_q):
                break
            if arm.composer == "pilot":
                claim, well_formed = composer.pilot_extract_claim(raw_q), None
            else:
                claim, well_formed = composer.clean_extract_claim(raw_q)
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
                    oracle_model, t_oracle, seed + 10000 + query_num, 32, kind="oracle")
                normalized = normalize(raw_reply)
                shown = f"Oracle result: {normalized}"
                messages.append({"role": "user", "content": shown})
            feedback_pairs.append((claim, shown if arm.placebo else normalized))
            exchanges.append({"raw_query_response": raw_q, "extracted_claim": claim,
                              "well_formed_claim": well_formed, "oracle_prompt": oracle_prompt,
                              "raw_oracle_reply": raw_reply, "normalized": normalized,
                              "placebo": arm.placebo})

    query_results = ""
    if exchanges:
        query_results = "VERIFICATION RESULTS:\n\n" + "\n\n".join(
            f"Query {i}: {e['extracted_claim']}\nResult: "
            f"{PLACEBO_TEXT if e['placebo'] else e['normalized']}"
            for i, e in enumerate(exchanges, 1))
    messages.append({"role": "user",
                     "content": judge_cfg["verdict_prompt"].format(query_results=query_results)})
    raw_verdict = client.complete(messages, judge_model, t_judge, seed + 99999, 512,
                                  kind="verdict")

    return records.build_record(
        transcript=transcript, arm=arm, budget=budget, replicate=replicate,
        position_a_is_correct=pos_a_correct, exchanges=exchanges,
        raw_verdict_text=raw_verdict, parses=parse_both(raw_verdict),
        judge_messages=messages + [{"role": "assistant", "content": raw_verdict}],
        seed=seed, judge_model=judge_model, oracle_model=oracle_model,
        dry_run=getattr(client, "dry_run", False), queries_used=len(exchanges))
