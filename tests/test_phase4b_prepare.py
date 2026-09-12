import copy

import pytest

from scripts import phase4b_prepare as preparation


@pytest.fixture
def history():
    templates = {"query": "Remaining {remaining_budget}/{total_budget}:\n{previous_queries}\nAsk.",
                 "verdict": "Decide:\n{query_results}\nFinal verdict please.", "rejection": "Retry the claim."}
    claim = "The café title contains Result: NO."
    block_feedback = "No factual verification was performed. NO is not an answer here."
    exchanges = [
        {"raw_query_response": "CLAIM: " + claim, "extracted_claim": claim,
         "normalized": "NO", "raw_oracle_reply": "NO", "placebo": False},
        {"raw_query_response": "CLAIM: blocked?", "extracted_claim": "blocked?", "blocked": True,
         "blocked_feedback": block_feedback, "raw_oracle_reply": None, "normalized": None, "placebo": False},
        {"raw_query_response": "CLAIM: " + claim, "extracted_claim": claim,
         "normalized": "YES", "raw_oracle_reply": "YES", "placebo": False},
    ]
    messages = [{"role": "system", "content": "Initial Oracle result: NO must stay untouched."},
                {"role": "user", "content": "Debate says Result: NO, but is not oracle feedback."}]
    events = []

    def previous(count):
        return "\n\n".join(f"Query {i+1}: {e['extracted_claim']}\nResult: " +
                           (e["blocked_feedback"] if e.get("blocked") else e["normalized"])
                           for i, e in enumerate(exchanges[:count]))

    for index, exchange in enumerate(exchanges):
        messages.append({"role": "user", "content": templates["query"].format(
            remaining_budget=8-index, total_budget=8, previous_queries=previous(index) if index else "No queries submitted yet.")})
        if index == 0:
            messages += [{"role": "assistant", "content": "Rejected query mentions NO."},
                         {"role": "user", "content": templates["rejection"]}]
            events.append({"raw_query": "Rejected query mentions NO.", "action": "retry", "slot_consumed": False})
        messages.append({"role": "assistant", "content": exchange["raw_query_response"]})
        shown = block_feedback if exchange.get("blocked") else "Oracle result: " + exchange["normalized"]
        messages.append({"role": "user", "content": shown})
        events.append({"raw_query": exchange["raw_query_response"], "action": "block" if exchange.get("blocked") else "allow",
                       "slot_consumed": True})
    messages += [{"role": "user", "content": templates["query"].format(remaining_budget=5, total_budget=8, previous_queries=previous(3))},
                 {"role": "assistant", "content": "DONE"},
                 {"role": "user", "content": templates["verdict"].format(query_results="VERIFICATION RESULTS:\n\n" + previous(3))}]
    return messages, {"budget": 8, "queries_used": 3, "exchanges": exchanges, "gate_events": events}, templates


def repair_map(messages, source, templates):
    spans = preparation.extract_repair_spans(messages, source, templates)
    return {"messages_sha256": preparation.digest_text(preparation.canonical(messages)),
            "spans": [{**span, "claim_id": "same-world-exact-claim"} for span in spans]}


def test_all_generated_occurrences_repaired_without_changing_queries_or_blocks(history):
    messages, source, templates = history
    mapping = repair_map(*history)
    assert len(mapping["spans"]) == 8
    assert sum(span["exchange_index"] == 0 for span in mapping["spans"]) == 5
    assert sum(span["exchange_index"] == 2 for span in mapping["spans"]) == 3
    assert not any(span["exchange_index"] == 1 for span in mapping["spans"])
    repaired = preparation.apply_repair_map(messages, mapping, {"same-world-exact-claim": "NOT ADDRESSED"})
    assert messages[:2] == repaired[:2]
    assert [m for m in messages if m["role"] == "assistant"] == [m for m in repaired if m["role"] == "assistant"]
    assert sum(m["content"] == "Oracle result: NOT ADDRESSED" for m in repaired) == 2
    assert sum(m["content"].count("\nResult: NOT ADDRESSED") for m in repaired) == 6
    blocked = source["exchanges"][1]["blocked_feedback"]
    assert sum(m["content"].count(blocked) for m in repaired) == sum(m["content"].count(blocked) for m in messages)
    claim = source["exchanges"][0]["extracted_claim"]
    assert sum(m["content"].count(claim) for m in repaired) == sum(m["content"].count(claim) for m in messages)
    assert preparation.apply_repair_map(messages, mapping, {}) == messages
    assert preparation.apply_repair_map(messages, mapping, {"same-world-exact-claim": "AMBIGUOUS"}) == messages


def test_each_source_occurrence_keeps_its_distinct_original_label(history):
    messages, source, templates = history
    mapping = repair_map(*history)
    assert {span["original_label"] for span in mapping["spans"]} == {"YES", "NO"}
    repaired = preparation.apply_repair_map(messages, mapping, {"same-world-exact-claim": "YES"})
    assert sum(m["content"] == "Oracle result: YES" for m in repaired) == 2
    assert all(messages[s["message_index"]]["content"][s["start"]:s["end"]] == s["original_label"] for s in mapping["spans"])


def test_unmatched_recap_and_early_done_are_rejected(history):
    messages, source, templates = history
    corrupt = copy.deepcopy(messages)
    corrupt[-1]["content"] = corrupt[-1]["content"].replace("\nResult: YES", "\nResult: NO")
    with pytest.raises(preparation.PreparationError, match="Unmatched user history"):
        preparation.extract_repair_spans(corrupt, source, templates)
    corrupt = copy.deepcopy(messages)
    corrupt[3]["content"] = "DONE"
    with pytest.raises(preparation.PreparationError, match="DONE precedes"):
        preparation.extract_repair_spans(corrupt, source, templates)


def test_hash_mismatch_and_overlapping_spans_rejected(history):
    messages, source, templates = history
    mapping = repair_map(*history)
    changed = copy.deepcopy(messages)
    changed[0]["content"] += "changed"
    with pytest.raises(preparation.PreparationError, match="history hash mismatch"):
        preparation.apply_repair_map(changed, mapping, {})
    mapping["spans"].append(copy.deepcopy(mapping["spans"][0]))
    with pytest.raises(preparation.PreparationError, match="Overlapping"):
        preparation.apply_repair_map(messages, mapping, {})


def test_dedup_identity_is_world_bound_and_exact_not_normalized():
    assert preparation.claim_id("world-a", "Café.") != preparation.claim_id("world-b", "Café.")
    assert preparation.claim_id("world-a", "Café.") != preparation.claim_id("world-a", "CAFÉ.")
    assert preparation.claim_id("world-a", "Café.") != preparation.claim_id("world-a", " Café.")


def test_coverage_counts_unique_claims_questions_and_packets():
    occurrences = [{"claim_id": f"c{i%20}", "question_id": f"q{i%10}", "packet_id": f"p{i}",
                    "original_label": "NO", "spans": [{}, {}]} for i in range(40)]
    labels = {f"c{i}": "NOT ADDRESSED" for i in range(20)}
    covered = preparation.repair_coverage(occurrences, labels)
    assert covered["minimum_feasibility_pass"]
    assert covered["changed_claims"] == 20 and covered["changed_questions"] == 10
    assert covered["changed_packets"] == 40 and covered["changed_label_spans"] == 80
    assert not preparation.repair_coverage(occurrences[:-1], labels)["minimum_feasibility_pass"]
    assert preparation.repair_coverage(occurrences + [occurrences[0]], labels)["changed_packets"] == 40
    assert preparation.repair_coverage(occurrences, {})["unadjudicated_claims"] == 20
    with pytest.raises(preparation.PreparationError, match="outside the prepared frame"):
        preparation.repair_coverage(occurrences, {"unknown-claim": "YES"})


def test_zero_exchange_done_history_has_no_edit_spans():
    templates = {"query": "{remaining_budget}/{total_budget}: {previous_queries}",
                 "verdict": "Result {query_results}", "rejection": "retry"}
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "debate"},
                {"role": "user", "content": "8/8: No queries submitted yet."},
                {"role": "assistant", "content": "DONE"}, {"role": "user", "content": "Result "}]
    assert preparation.extract_repair_spans(messages, {"budget": 8, "queries_used": 0, "exchanges": [], "gate_events": []}, templates) == []
