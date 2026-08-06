"""Resolving a frozen canary plan cell into the parameters needed to execute it.

`enumerate_canary_cells` says *what* the 945 cells are; it deliberately says nothing about how
to run one. This resolver closes that gap, and it exists because three of the joins are traps:

- the canary's transcript condition strings are not valid `debate_gen` protocol names, so a
  runner that passes them straight through raises;
- `generate_transcript` computes its own cell key in a different scheme from `phase2_plan`, so
  a record persisted under it loses its link to the plan and the manifest;
- one cell carries a symbolic judge model that must never be dispatched literally.

Everything resolvable from the frozen protocol is read from it rather than inferred, so the
protocol stays the single source of truth for oracle mode and query budget.
"""
import json

import pytest

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.debate_gen import PROTOCOL_WORD_CAPS


ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"


def _protocol():
    with open("rejudge/phase2_protocol.json", encoding="utf-8") as handle:
        return json.load(handle)


def _bundle():
    with open("rejudge/phase2_prompt_bundle.json", encoding="utf-8") as handle:
        return json.load(handle)


def _plan_cells():
    return phase2_plan.enumerate_canary_cells(_protocol())


def _by(kind, condition):
    for cell in _plan_cells():
        if cell["kind"] == kind and cell["condition"] == condition:
            return cell
    raise AssertionError(f"no cell for {kind}/{condition}")


def _resolve(cell, anchor_judge_model=ANCHOR):
    return cells_mod.resolve_cell(cell, _protocol(), _bundle(),
                                  anchor_judge_model=anchor_judge_model)


# --- transcripts -------------------------------------------------------------------------

def test_the_uncapped_transcript_condition_maps_to_a_real_debate_gen_protocol():
    resolved = _resolve(_by("canary_debate_transcript", "canary_blind_uncapped_3_round"))
    assert resolved.is_transcript is True
    assert resolved.transcript_protocol_name == "uncapped3"
    assert resolved.transcript_protocol_name in PROTOCOL_WORD_CAPS


def test_the_capped_transcript_condition_maps_to_the_150_word_protocol():
    resolved = _resolve(
        _by("canary_capped_debate_transcript", "canary_blind_capped150_3_round"))
    assert resolved.transcript_protocol_name == "capped3"
    assert PROTOCOL_WORD_CAPS[resolved.transcript_protocol_name] == 150


def test_a_transcript_cell_keeps_the_plan_cell_key_not_the_debate_gen_one():
    # debate_gen builds its own key as "{protocol}|{debater}|{question}|{index}", a different
    # scheme from the plan's "{namespace}:{kind}:{hash}". Persisting debate_gen's key would
    # break the link to planning_cell_key and the manifest, so the resolver must carry the
    # plan's through untouched.
    cell = _by("canary_debate_transcript", "canary_blind_uncapped_3_round")
    resolved = _resolve(cell)
    assert resolved.cell_key == cell["cell_key"]

    debate_gen_key = (f"{resolved.transcript_protocol_name}|{resolved.debater_model}"
                      f"|{resolved.question_id}|{resolved.transcript_index}")
    assert resolved.cell_key != debate_gen_key
    assert ":" in resolved.cell_key and "|" in debate_gen_key


# --- judgment conditions -----------------------------------------------------------------

def test_b0_runs_no_query_loop():
    resolved = _resolve(_by("canary_debate_judgment", "b0"))
    assert resolved.query_budget == 0
    assert resolved.oracle_mode == "none"
    assert resolved.produces_queries is False


def test_sequential_b2_is_a_clean_query_producing_arm():
    resolved = _resolve(_by("canary_debate_judgment", "sequential_b2"))
    assert (resolved.query_budget, resolved.oracle_mode) == (2, "clean")
    assert resolved.produces_queries is True
    assert resolved.arm_name == "clean"


def test_placebo_b2_produces_queries_but_dispatches_no_oracle():
    resolved = _resolve(_by("canary_debate_judgment", "placebo_b2"))
    assert (resolved.query_budget, resolved.oracle_mode) == (2, "placebo")
    assert resolved.produces_queries is True
    assert resolved.arm_name == "placebo"


def test_batch_same_qa_replays_rather_than_producing_queries():
    resolved = _resolve(_by("canary_debate_judgment", "batch_same_qa_b2"))
    assert resolved.oracle_mode == "replay_clean_qa"
    assert resolved.produces_queries is False
    # It replays the paired sequential judgment, so it carries a second dependency.
    assert len(resolved.dependency_keys) == 2


def test_no_debate_clean_b2_produces_queries_without_a_transcript():
    resolved = _resolve(_by("canary_no_debate_judgment", "clean_b2"))
    assert resolved.produces_queries is True
    assert resolved.oracle_mode == "clean"
    assert resolved.debater_model is None
    assert resolved.dependency_keys == ()


def test_the_specials_run_no_query_loop():
    for kind, condition in [
        ("canary_cap_protection_judgment", "capped150_b0"),
        ("canary_empty_evidence_judgment", "empty_evidence_table"),
        ("canary_full_document_judgment", "full_document_ceiling"),
    ]:
        resolved = _resolve(_by(kind, condition))
        assert resolved.produces_queries is False, condition
        assert resolved.oracle_mode == "none", condition


# --- the symbolic anchor -------------------------------------------------------------------

def test_the_symbolic_anchor_cell_resolves_to_the_approved_judge():
    symbolic = [c for c in _plan_cells()
                if str(c.get("judge_model", "")).startswith("selector:")]
    assert len(symbolic) == 1
    assert _resolve(symbolic[0]).judge_model == ANCHOR


def test_a_symbolic_anchor_cell_refuses_to_resolve_without_the_approved_judge():
    symbolic = [c for c in _plan_cells()
                if str(c.get("judge_model", "")).startswith("selector:")][0]
    with pytest.raises(cells_mod.UnresolvedAnchor):
        _resolve(symbolic, anchor_judge_model=None)


def test_a_resolved_judge_model_is_never_symbolic():
    for cell in _plan_cells():
        resolved = _resolve(cell)
        assert not str(resolved.judge_model or "").startswith("selector:")


# --- fail closed ---------------------------------------------------------------------------

def test_an_unknown_condition_is_refused():
    cell = dict(_by("canary_debate_judgment", "b0"), condition="invented_condition")
    with pytest.raises(cells_mod.UnresolvableCell):
        _resolve(cell)


def test_an_unknown_kind_is_refused():
    cell = dict(_by("canary_debate_judgment", "b0"), kind="invented_kind")
    with pytest.raises(cells_mod.UnresolvableCell):
        _resolve(cell)


# --- the whole plan -------------------------------------------------------------------------

def test_every_one_of_the_945_frozen_cells_resolves():
    resolved = [_resolve(cell) for cell in _plan_cells()]
    assert len(resolved) == 945
    assert sum(1 for r in resolved if r.is_transcript) == 50
    assert sum(1 for r in resolved if r.produces_queries) == 336


def test_the_query_producing_cells_are_exactly_the_four_gated_conditions():
    producing = {(r.kind, r.condition) for r in
                 (_resolve(cell) for cell in _plan_cells()) if r.produces_queries}
    assert producing == {
        ("canary_debate_judgment", "sequential_b2"),
        ("canary_debate_judgment", "placebo_b2"),
        ("canary_no_debate_judgment", "clean_b2"),
        ("canary_no_debate_judgment", "placebo_b2"),
    }


def test_the_frozen_query_generation_call_count_is_reproduced():
    # The frozen cost model fixes canary query-generation calls at 672 before retries.
    total = sum(r.query_budget for r in
                (_resolve(cell) for cell in _plan_cells()) if r.produces_queries)
    assert total == 672


# --- main-run cells ---------------------------------------------------------------------
#
# The canary plan prefixes every kind and transcript condition with "canary_"; the main plan
# does not. The resolver was keyed on the prefixed names, so every one of the 23,200 main
# cells raised UnresolvableCell. The manifest for the main run validated cleanly while nothing
# could execute it, which is the sort of gap a manifest check cannot catch.

def _main_cells():
    from rejudge.phase2_main_manifest import enumerate_main_cells
    return enumerate_main_cells(".")


def test_a_main_run_judgment_cell_resolves():
    from rejudge import phase2_canary_cells as cells_mod

    protocol, bundle = _protocol(), _bundle()
    cell = next(c for c in _main_cells()
                if c["kind"] == "debate_judgment" and c["condition"] == "sequential_b2")
    resolved = cells_mod.resolve_cell(cell, protocol, bundle,
                                      anchor_judge_model=ANCHOR)
    assert resolved.kind == "debate_judgment"
    assert resolved.query_budget == 2
    assert resolved.produces_queries


def test_a_main_run_transcript_cell_resolves_to_a_debate_gen_protocol():
    from rejudge import phase2_canary_cells as cells_mod

    protocol, bundle = _protocol(), _bundle()
    cell = next(c for c in _main_cells() if c["kind"] == "debate_transcript")
    resolved = cells_mod.resolve_cell(cell, protocol, bundle, anchor_judge_model=ANCHOR)
    assert resolved.is_transcript
    assert resolved.transcript_protocol_name == "uncapped3"


def test_every_main_run_cell_kind_resolves():
    """All 23,200, not a sample: an unresolvable kind discovered mid-run is a stopped run."""
    from rejudge import phase2_canary_cells as cells_mod

    protocol, bundle = _protocol(), _bundle()
    kinds = {}
    for cell in _main_cells():
        if cell["kind"] == "capability_qa":
            continue  # ran under the capability preflight, not this executor
        kinds.setdefault((cell["kind"], cell["condition"]), cell)
    for (kind, condition), cell in sorted(kinds.items()):
        cells_mod.resolve_cell(cell, protocol, bundle, anchor_judge_model=ANCHOR)


def test_the_canary_kinds_still_resolve():
    """Two completed runs depend on the prefixed names continuing to work."""
    from rejudge import phase2_canary_cells as cells_mod
    from rejudge.phase2_plan import enumerate_canary_cells

    protocol, bundle = _protocol(), _bundle()
    for cell in enumerate_canary_cells(protocol)[:40]:
        cells_mod.resolve_cell(cell, protocol, bundle, anchor_judge_model=ANCHOR)
