import copy
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import pytest

from scripts import phase4b_prepare_recipients as prep


INPUTS = Path(r"E:\selvarath-archive\phase4-preparation-2026-09-12")
CLAIMS = Path(r"E:\selvarath-archive\phase4b-preparation-2026-09-12")
LABELS = Path(r"E:\selvarath-archive\phase4b-blinded-adjudication-2026-09-12\analysis-ai-source-review-2026-09-12\repair_labels.json")


@pytest.fixture
def small_panel():
    unit = {"unit_id": "private-unit", "question_id": "private-question", "world": "private-world",
            "debater": "private-debater", "transcript_index": 0, "side": 0, "correct_position": "A"}
    packets, maps = [], []
    for donor in prep.DONORS:
        messages = [{"role": "system", "content": "Judge. Oracle result: NO is quoted here."},
                    {"role": "user", "content": "Debate text says Result: NO."},
                    {"role": "assistant", "content": "Claim: café means NO?"},
                    {"role": "user", "content": "Oracle result: NO"},
                    {"role": "assistant", "content": "Rejected NO query"},
                    {"role": "user", "content": "Blocked feedback NO must be preserved."},
                    {"role": "user", "content": "Café recap\nResult: NO\nResult: YES\nChoose a final verdict."}]
        spans = []
        for index, label, occurrence, exchange in [(3, "NO", "immediate", 0), (6, "NO", "final_recap", 0),
                                                   (6, "YES", "final_recap", 1)]:
            start = messages[index]["content"].index(label)
            spans.append({"message_index": index, "start": start, "end": start + len(label),
                          "original_label": label, "claim_id": "change" if exchange == 0 else "same",
                          "exchange_index": exchange, "occurrence_kind": occurrence})
        packet_id = unit["unit_id"] + ":" + donor
        packet = {"packet_id": packet_id, "source_cell_key": "source-" + donor,
                  "messages": messages, "messages_sha256": prep.message_sha(messages)}
        mapping = {key: packet[key] for key in ("packet_id", "source_cell_key", "messages_sha256")}
        mapping.update({key: unit[key] for key in ("unit_id", "question_id", "world", "debater")})
        mapping.update(arm=donor, spans=spans, blocked_exchanges=[{"exchange_index": 2}])
        packets.append(packet)
        maps.append(mapping)
    return [unit], packets, maps, {"change": "NOT ADDRESSED", "same": "YES"}


def test_literal_edits_preserve_queries_blocks_unicode_and_nonlabel_text(small_panel):
    _, sources, maps, labels = small_panel
    before = copy.deepcopy(sources[0])
    repaired, edits = prep.repair_packet(sources[0], maps[0], labels)
    assert sources[0] == before
    assert len(edits) == 2
    assert repaired[:3] == before["messages"][:3]
    assert repaired[4:6] == before["messages"][4:6]
    assert repaired[3]["content"] == "Oracle result: NOT ADDRESSED"
    assert repaired[6]["content"] == "Café recap\nResult: NOT ADDRESSED\nResult: YES\nChoose a final verdict."
    for edit in edits:
        assert before["messages"][edit["message_index"]]["content"][edit["original_start"]:edit["original_end"]] == edit["original_label"]
        assert repaired[edit["message_index"]]["content"][edit["repaired_start"]:edit["repaired_end"]] == edit["repaired_label"]


def test_ambiguous_and_same_labels_still_get_distinct_fresh_arms(small_panel):
    units, sources, maps, _ = small_panel
    packets, calls, edits, coverage = prep.build_panel(units, sources, maps, {"change": "AMBIGUOUS", "same": "YES"})
    assert len(packets) == 4 and len(calls) == len({call["cell_id"] for call in calls}) == 8
    assert coverage["changed_packets"] == coverage["changed_label_spans"] == 0
    assert all(not row["changed"] and row["edits"] == [] for row in edits)
    assert len({packet["messages_sha256"] for packet in packets}) == 1
    assert {call["arm"] for call in calls} == set(prep.ARMS)
    for judge in prep.JUDGES:
        assert len({call["seed"] for call in calls if call["judge"] == judge}) == 1


def test_no_private_metadata_is_added_to_provider_messages(small_panel):
    packets, calls, _, _ = prep.build_panel(*small_panel)
    for packet in packets:
        text = json.dumps(packet["messages"])
        assert all(marker not in text for marker in ("private-unit", "private-question", "private-world", "private-debater", "correct_position"))
        assert all(set(message) == {"role", "content"} for message in packet["messages"])
        assert packet["messages"][-1]["role"] == "user"
    for call in calls:
        assert "top_p" not in call and "reasoning_effort" not in call
        assert all(call[key] == value for key, value in prep.MODEL_SETTINGS[call["judge"]].items())


@pytest.mark.parametrize("corruption", ["missing", "extra", "duplicate", "wrong_unit", "saved_verdict", "wrong_hash"])
def test_ineligible_or_corrupt_history_is_rejected(small_panel, corruption):
    units, sources, maps, labels = small_panel
    if corruption == "missing":
        maps.pop()
    elif corruption == "extra":
        extra = copy.deepcopy(maps[0]); extra["packet_id"] = "extra"
        maps.append(extra)
    elif corruption == "duplicate":
        sources.append(copy.deepcopy(sources[0]))
    elif corruption == "wrong_unit":
        maps[0]["question_id"] = "other-question"
    elif corruption == "saved_verdict":
        sources[0]["messages"].append({"role": "assistant", "content": "VERDICT: A"})
        sources[0]["messages_sha256"] = maps[0]["messages_sha256"] = prep.message_sha(sources[0]["messages"])
    else:
        sources[0]["messages"][0]["content"] += "corrupt"
    with pytest.raises(ValueError):
        prep.build_panel(units, sources, maps, labels)


def test_duplicate_or_shared_context_edit_is_rejected(small_panel):
    _, sources, maps, labels = small_panel
    duplicate = copy.deepcopy(maps[0])
    duplicate["spans"].append(copy.deepcopy(duplicate["spans"][0]))
    with pytest.raises(ValueError, match="Overlapping"):
        prep.repair_packet(sources[0], duplicate, labels)
    context = copy.deepcopy(maps[0])
    index = sources[0]["messages"][1]["content"].index("NO")
    context["spans"].append({**context["spans"][0], "message_index": 1, "start": index, "end": index + 2})
    with pytest.raises(ValueError, match="shared debate context"):
        prep.repair_packet(sources[0], context, labels)


@pytest.fixture(scope="module")
def full_panel():
    if not all(path.exists() for path in (INPUTS, CLAIMS, LABELS)):
        pytest.skip("Frozen private source archive unavailable")
    artifacts, manifest = prep.prepare(INPUTS, CLAIMS, LABELS)
    return artifacts, manifest


def test_full_frozen_panel_exact_originals_full_cross_and_paired_seeds(full_panel):
    artifacts, manifest = full_panel
    packets = prep.read_rows(artifacts["packets.jsonl"])
    calls = prep.read_rows(artifacts["calls.jsonl"])
    units = prep.read_rows(artifacts["units_private.jsonl"])
    sources = {row["packet_id"]: row for row in prep.read_rows((INPUTS / "packets.jsonl").read_bytes())}
    assert (len(units), len(packets), len(calls)) == (656, 2624, 5248)
    assert artifacts["units_private.jsonl"] == (INPUTS / "units_private.jsonl").read_bytes()
    assert manifest["repair_coverage"] == {"changed_claims": 549, "changed_packets": 588, "changed_questions": 80,
            "changed_answered_exchanges": 761, "changed_label_spans": 3896, "blocked_exchanges_preserved": 1704,
            "donor_packets": 1312, "unchanged_packets": 724}
    for packet in packets:
        source = sources[packet["source_packet_id"]]
        assert packet["messages"][:2] == source["messages"][:2]
        if packet["repair_status"] == "original":
            assert prep.canonical(packet["messages"]).encode() == prep.canonical(source["messages"]).encode()
    assert Counter((call["judge"], call["arm"]) for call in calls) == {(judge, arm): 656 for judge in prep.JUDGES for arm in prep.ARMS}
    q_by_unit = {unit["unit_id"]: unit["question_id"] for unit in units}
    assert len({calls[block]["cell_id"] for block in range(0, 5248, 64)}) == 82
    seen_questions = set()
    seeds = defaultdict(set)
    for start in range(0, 5248, 64):
        block_questions = {q_by_unit[call["unit_id"]] for call in calls[start:start + 64]}
        assert len(block_questions) == 1 and not block_questions & seen_questions
        seen_questions.update(block_questions)
    for call in calls:
        seed_bytes = ("phase4b-recipient-verdict-v1|" + call["unit_id"] + "|" + call["judge"]).encode()
        assert call["seed"] == int(hashlib.sha256(seed_bytes).hexdigest()[:8], 16) % 2147483647
        seeds[(call["unit_id"], call["judge"])].add(call["seed"])
    assert len(seeds) == 1312 and all(len(value) == 1 for value in seeds.values())
    assert manifest["paid_execution_authorized"] is False and manifest["independent_human_validation"] is False


def test_full_edit_map_preserves_every_intervening_character(full_panel):
    artifacts, _ = full_panel
    packets = {row["packet_id"]: row for row in prep.read_rows(artifacts["packets.jsonl"])}
    edits = prep.read_rows(artifacts["edit_map_private.jsonl"])
    assert len(edits) == 1312
    for row in edits:
        original = packets[row["original_packet_id"]]["messages"]
        repaired = packets[row["repaired_packet_id"]]["messages"]
        per_message = defaultdict(list)
        for edit in row["edits"]:
            per_message[edit["message_index"]].append(edit)
        for index, (before, after) in enumerate(zip(original, repaired, strict=True)):
            assert before["role"] == after["role"]
            old_cursor, new_cursor = 0, 0
            for edit in per_message[index]:
                assert before["role"] == "user" and index >= 2
                assert before["content"][old_cursor:edit["original_start"]] == after["content"][new_cursor:edit["repaired_start"]]
                assert before["content"][edit["original_start"]:edit["original_end"]] == edit["original_label"]
                assert after["content"][edit["repaired_start"]:edit["repaired_end"]] == edit["repaired_label"]
                old_cursor, new_cursor = edit["original_end"], edit["repaired_end"]
            assert before["content"][old_cursor:] == after["content"][new_cursor:]


def test_full_build_reproduces_all_artifact_bytes(full_panel):
    again = prep.prepare(INPUTS, CLAIMS, LABELS)
    assert again == full_panel


def test_changed_reviewed_labels_refused_before_materialization(full_panel, tmp_path):
    changed = tmp_path / "repair_labels.json"
    document = json.loads(LABELS.read_bytes())
    key = next(iter(document["labels"]))
    document["labels"][key] = "YES" if document["labels"][key] != "YES" else "NO"
    changed.write_text(json.dumps(document))
    with pytest.raises(prep.PreparationError, match="Source hash mismatch: repair_labels.json"):
        prep.prepare(INPUTS, CLAIMS, changed)
