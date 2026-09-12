import copy
import hashlib
import json
from pathlib import Path

import pytest

from rejudge.phase4b_labels import call_seed, human_rank, messages_for
from scripts import phase4b_consensus as c


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture_panel(count=21):
    models = sorted(c.MODELS)
    world = "\n".join(f"The archive entry {i} is blue." for i in range(count))
    world_sha = digest(world)
    claims, calls, packets, occurrences = [], [], [], []
    maps = [{"packet_id": f"p{i}", "question_id": f"q{i // 4}", "world": "synthetic",
             "world_sha256": world_sha, "arm": "qwen_history" if i % 2 == 0 else "llama_history"}
            for i in range(1312)]
    settings = {model: {"temperature": 1.0, "top_p": 1.0, "max_tokens": 8192,
                        "reasoning_effort": "medium" if model.startswith("openai") else "low"} for model in models}
    for i in range(count):
        claim = f"The archive entry {i} is blue."
        cid = digest(world_sha + "|" + claim)
        claims.append({"claim_id": cid, "world_sha256": world_sha, "exact_claim": claim})
        messages = messages_for(world, claim, "FROZEN ORACLE CONTRACT")
        messages_sha = digest(c._canonical(messages))
        packets.append({"claim_id": cid, "packet_id": cid, "messages": messages, "messages_sha256": messages_sha})
        for model in models:
            calls.append({"cell_id": cid + ":" + model, "claim_id": cid, "judge": model,
                          "packet_id": cid, "messages_sha256": messages_sha,
                          "seed": call_seed(cid, model), "stream": False, **settings[model]})
        for index in (2 * i, 2 * i + 1):
            packet = maps[index]
            occurrences.append({"claim_id": cid, "packet_id": packet["packet_id"],
                                "question_id": packet["question_id"], "world_sha256": world_sha,
                                "donor_arm": packet["arm"], "exchange_index": 0,
                                "original_label": "NO", "spans": [{"original_label": "NO"}]})
    manifest = {"models": models, "model_settings": settings, "oracle_contract": "FROZEN ORACLE CONTRACT",
                "distinct_claims": count, "adjudication_calls": 2 * count}
    panel = c.validate_panel(claims, [{"world_sha256": world_sha, "world_document": world}],
                             calls, packets, occurrences, maps, manifest)
    rows = []
    for call in calls:
        raw = json.dumps({"label": "YES", "unambiguous": True,
                          "quotes": [panel["claims"][call["claim_id"]]["exact_claim"]],
                          "rationale": "Directly stated.", "missing_information": ""})
        rows.append({**{key: call[key] for key in ("cell_id", "claim_id", "judge", "packet_id", "messages_sha256")},
                     "raw_adjudication_text": raw, "returned_model_id": call["judge"], "configuration_valid": True,
                     "completed_at": "2026-09-12T12:00:00+00:00", "attempt_id": "attempt:" + call["cell_id"]})
    return panel, rows


def analyze(panel, rows, review=None):
    return c.analyze(panel, rows, input_manifest_sha256="input-hash", results_sha256="result-hash", human_review=review)


def approved(template):
    review = copy.deepcopy(template)
    review["reviewer"] = {"name": "Example human", "role": "human",
                          "provenance": "Personally checked the full frozen sources and quotations.",
                          "reviewed_at": "2026-09-12T12:00:00Z"}
    for entry in review["entries"]:
        entry["decision"] = "agree"
    return review


def test_complete_panel_still_requires_human_review_and_never_authorizes_spend(tmp_path):
    panel, rows = fixture_panel()
    summary, claim_rows, template, repair = analyze(panel, rows)
    assert summary["status"] == "awaiting_human_review"
    assert summary["pre_human_feasibility_pass"] is True
    assert summary["coverage_before_human_review"]["changed_packets"] == 42
    assert summary["coverage_before_human_review"]["maximum_mean_causal_benefit"] == 42 / 1312
    assert repair["labels"] == {} and repair["recipient_evaluation_authorized"] is False
    expected = sorted(panel["claims"], key=lambda cid: (human_rank(panel["claims"][cid]["world_sha256"],
                                                                panel["claims"][cid]["exact_claim"]), cid))[:20]
    assert [entry["claim_id"] for entry in template["entries"]] == expected
    text = c.human_markdown(panel, claim_rows, template, tmp_path)
    assert "Directly stated." in text and "Full frozen world" in text
    assert "original_label" not in json.dumps(template) and "qwen_history" not in text and "llama_history" not in text
    assert all(entry["decision"] == "pending" for entry in template["entries"])
    reviewed, _, _, released = analyze(panel, rows, approved(template))
    assert reviewed["status"] == "repair_labels_released"
    assert len(released["labels"]) == 21 and set(released["labels"].values()) == {"YES"}
    assert reviewed["recipient_evaluation_authorized"] is False


def test_administrative_missing_never_becomes_scientific_ambiguity_or_pass():
    panel, rows = fixture_panel()
    summary, claim_rows, template, repair = analyze(panel, rows[:-1])
    assert summary["status"] == "incomplete"
    assert summary["administrative_coverage"]["missing_calls"] == 1
    assert summary["coverage_before_human_review"]["unadjudicated_claims"] == 1
    assert summary["coverage_before_human_review"]["ambiguous_claims"] == 0
    assert summary["pre_human_feasibility_pass"] is False
    assert summary["minimum_feasibility_and_human_review_pass"] is False
    assert template["entries"] == [] and repair["labels"] == {}
    assert next(row for row in claim_rows if row["claim_id"] == rows[-1]["claim_id"])["consensus"]["label"] is None


def test_completed_malformed_response_is_excluded_without_missingness_or_retry():
    panel, rows = fixture_panel()
    rows[0]["raw_adjudication_text"] = '{"label": "YES", "label": "NO"}'
    summary, _, template, _ = analyze(panel, rows)
    assert summary["administrative_coverage"]["complete"] is True
    coverage = summary["coverage_before_human_review"]
    assert coverage["unadjudicated_claims"] == 0 and coverage["ambiguous_claims"] == 1
    assert coverage["changed_claims"] == 20
    group = summary["exclusions_and_coverage_by"]["world"][0]
    assert group["ambiguous_or_excluded_claims"] == 1 and group["ambiguous_or_excluded_fraction"] == 1 / 21
    released = analyze(panel, rows, approved(template))[3]
    assert released["status"] == "released" and released["labels"][rows[0]["claim_id"]] == "AMBIGUOUS"


def test_disagreement_excludes_claim_and_rechecks_coverage_without_replacing_sample():
    panel, rows = fixture_panel()
    template = analyze(panel, rows)[2]
    review = approved(template)
    rejected_id = review["entries"][0]["claim_id"]
    review["entries"][0].update(decision="disagree", comment="The cited source does not support the scope of this claim.")
    summary, _, retained_template, repair = analyze(panel, rows, review)
    assert retained_template == template
    assert summary["human_review"]["complete"] is True
    assert summary["coverage_after_human_exclusions"]["ambiguous_claims"] == 1
    assert summary["coverage_after_human_exclusions"]["unadjudicated_claims"] == 0
    assert summary["coverage_after_human_exclusions"]["changed_packets"] == 40
    assert repair["status"] == "released" and repair["labels"][rejected_id] == "AMBIGUOUS"
    review["entries"][1].update(decision="disagree", comment="Direct contradiction is not established.")
    summary, _, retained_template, repair = analyze(panel, rows, review)
    assert retained_template == template and summary["status"] == "insufficient_repair_coverage"
    assert repair["labels"] == {}


def test_original_labels_that_disagree_are_counted_per_occurrence():
    panel, rows = fixture_panel()
    panel["occurrences"][0]["original_label"] = "YES"
    coverage = analyze(panel, rows)[0]["coverage_before_human_review"]
    assert coverage["changed_claims"] == 21
    assert coverage["changed_packets"] == coverage["changed_answered_exchanges"] == 41


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "wrong_sample", "wrong_claim", "model_reviewer", "no_provenance", "bad_decision"])
def test_human_review_rejects_unbound_or_nonhuman_acceptance(mutation):
    panel, rows = fixture_panel()
    template = analyze(panel, rows)[2]
    review = approved(template)
    if mutation == "missing": review["entries"].pop()
    elif mutation == "duplicate": review["entries"].append(copy.deepcopy(review["entries"][0]))
    elif mutation == "unknown": review["entries"][0]["claim_id"] = "unknown"
    elif mutation == "wrong_sample": review["sample_id"] = "other-results"
    elif mutation == "wrong_claim": review["entries"][0]["exact_claim"] = "Reworded claim"
    elif mutation == "model_reviewer": review["reviewer"]["role"] = "assistant"
    elif mutation == "no_provenance": review["reviewer"]["provenance"] = ""
    elif mutation == "bad_decision": review["entries"][0]["decision"] = ["agree"]
    with pytest.raises(c.ConsensusError): analyze(panel, rows, review)


def test_pending_human_entry_blocks_release_even_with_other_agreements():
    panel, rows = fixture_panel()
    template = analyze(panel, rows)[2]
    review = approved(template)
    review["entries"][-1]["decision"] = "pending"
    summary, _, _, repair = analyze(panel, rows, review)
    assert summary["human_review"]["reviewed"] == 19
    assert summary["status"] == "awaiting_human_review" and repair["labels"] == {}


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "wrong_model", "wrong_config", "wrong_hash", "wrong_claim", "conflicting_raw"])
def test_completed_result_identity_is_strict(mutation):
    panel, rows = fixture_panel(1)
    if mutation == "duplicate": rows.append(copy.deepcopy(rows[0]))
    elif mutation == "unknown": rows[0]["cell_id"] = "unknown"
    elif mutation == "wrong_model": rows[0]["returned_model_id"] = "wrong-model"
    elif mutation == "wrong_config": rows[0]["configuration_valid"] = False
    elif mutation == "wrong_hash": rows[0]["messages_sha256"] = "wrong-hash"
    elif mutation == "wrong_claim": rows[0]["claim_id"] = "wrong-claim"
    elif mutation == "conflicting_raw": rows[0]["raw_verdict_text"] = "conflicting"
    with pytest.raises(c.ConsensusError): analyze(panel, rows)


def write_inputs(directory, panel):
    directory.mkdir()
    data = {"blind_claims.jsonl": list(panel["claims"].values()), "worlds_private.jsonl": list(panel["worlds"].values()),
            "calls.jsonl": list(panel["calls"].values()), "claim_packets.jsonl": list(panel["packets"].values()),
            "claim_occurrences_private.jsonl": panel["occurrences"], "packet_repair_map_private.jsonl": list(panel["packet_maps"].values())}
    (directory / "manifest.json").write_text("{}\n", encoding="utf-8")
    for name, values in data.items():
        (directory / name).write_text("".join(c._canonical(row) + "\n" for row in values), encoding="utf-8")
    manifest = {**panel["manifest"], "schema_version": "phase4b_blinded_adjudication_panel_v1",
                "prompt_script_sha256": c._sha(c.ROOT / "rejudge/phase4b_labels.py"),
                "outputs": {name: {"sha256": c._sha(directory / name), "bytes": (directory / name).stat().st_size}
                            for name in c.REQUIRED_FILES}}
    (directory / "adjudication_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_cli_writes_blinded_packet_preserves_review_and_releases_only_after_explicit_input(tmp_path, monkeypatch):
    panel, rows = fixture_panel()
    inputs, out, result_path = tmp_path / "inputs", tmp_path / "out", tmp_path / "results.jsonl"
    write_inputs(inputs, panel)
    result_path.write_text("".join(c._canonical(row) + "\n" for row in rows), encoding="utf-8")
    argv = ["phase4b_consensus", "--inputs", str(inputs), "--results", str(result_path), "--out", str(out)]
    monkeypatch.setattr(c.sys, "argv", argv)
    assert c.main() == 0
    review_path = out / "human_review.json"
    template = json.loads(review_path.read_text())
    assert all(row["decision"] == "pending" for row in template["entries"])
    document = approved(template)
    review_path.write_text(json.dumps(document), encoding="utf-8")
    preserved = review_path.read_bytes()
    monkeypatch.setattr(c.sys, "argv", argv + ["--human-review", str(review_path)])
    assert c.main() == 0
    assert review_path.read_bytes() == preserved
    assert json.loads((out / "repair_labels.json").read_text())["status"] == "released"
    world = next(iter(panel["worlds"].values()))
    assert (out / ("human-world-" + world["world_sha256"] + ".txt")).read_bytes() == world["world_document"].encode()


def test_modified_prepared_file_is_rejected(tmp_path):
    panel, _ = fixture_panel(1)
    inputs = tmp_path / "inputs"
    write_inputs(inputs, panel)
    (inputs / "blind_claims.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(c.ConsensusError, match="Prepared source changed"):
        c.load_inputs(inputs)


def test_strict_json_rejects_nonstandard_constants():
    with pytest.raises(c.ConsensusError): c._json('{"reviewer": NaN}')
    with pytest.raises(c.ConsensusError): c._json('{"entries": [], "entries": []}')
