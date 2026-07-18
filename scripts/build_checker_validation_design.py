"""One-shot builder for rejudge/phase2_checker_validation_design_2026-07-18.json.

Not part of the frozen module surface; a throwaway script kept for reproducibility of
the design artifact's embedded hashes. Run with: python scripts/build_checker_validation_design.py
"""
from __future__ import annotations

import json
from pathlib import Path

from rejudge import phase2_plan, phase2_prompt_bundle
from rejudge.phase2_checker_sampling import (
    BOUNDARY_HEURISTICS,
    BOUNDARY_PER_HEURISTIC,
    BOUNDARY_TOTAL,
    CALIBRATION_FILES,
    DEFAULT_PRIMARY_SET_PATH,
    DEFAULT_REGRESSION_SET_PATH,
    DEFAULT_RESERVE_POOL_PATH,
    PRIMARY_TOTAL,
    REGRESSION_CANDIDATE_RESTATEMENT_DESIGN_TARGET,
    REGRESSION_META_OR_EVALUATIVE_DESIGN_TARGET,
    REGRESSION_TOTAL,
    RESERVE_MINIMUM,
    SEED,
    SYNTHETIC_CATEGORIES,
    SYNTHETIC_PAIR_COUNT,
    SYNTHETIC_TOTAL,
    WORLD_STRATIFIED_PER_WORLD,
    build_all,
    canonical_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_PATH = REPO_ROOT / "rejudge" / "phase2_checker_validation_design_2026-07-18.json"


def build_design() -> dict:
    bundle, protocol = phase2_prompt_bundle.load_and_validate()
    built = build_all()

    checker_template = bundle["templates"]["query_checker"]
    checker_template_sha256 = phase2_plan.canonical_sha256(checker_template)
    bundle_sha256 = phase2_plan.canonical_sha256(bundle)

    candidate_pool = [
        "Qwen/Qwen2.5-7B-Instruct-Turbo",
        "google/gemma-4-31B-it",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "openai/gpt-oss-120b",
    ]

    regression_counts = built.regression_set["counts"]

    design = {
        "schema_version": "phase2_checker_validation_design_v1",
        "protocol_id": protocol["protocol_id"],
        "status": "frozen_before_any_checker_call",
        "execution_authorized": False,
        "frozen_at": "2026-07-18",
        "note": (
            "Implements the oracle-frozen query-checker validation design exactly as "
            "specified, with one reported (not silently patched) infeasibility: see "
            "regression_set.frozen_design_vs_actual below."
        ),
        "candidate_models": {
            "pool": candidate_pool,
            "temperature": 0,
            "checker_prompt": {
                "source": "rejudge/phase2_prompt_bundle.json#/templates/query_checker",
                "bound_via": (
                    "rejudge.phase2_prompt_bundle.load_and_validate + "
                    "rejudge.phase2_plan.canonical_sha256"
                ),
                "prompt_bundle_id": bundle["bundle_id"],
                "prompt_bundle_status": bundle["status"],
                "prompt_bundle_canonical_sha256": bundle_sha256,
                "query_checker_template_canonical_sha256": checker_template_sha256,
                "system_prompt": checker_template["system_prompt"],
                "user_prompt_template": checker_template["user_prompt_template"],
            },
        },
        "label_taxonomy": {
            "values": ["allow", "reject", "unresolved"],
            "semantics": {
                "allow": "human judges the query an atomic, checkable, non-prohibited factual claim",
                "reject": "human judges the query prohibited under docs/oracle-query-contract.md",
                "unresolved": (
                    "human cannot make the allow/reject call unambiguously; distinct from "
                    "reject -- at runtime CheckerDecision.UNRESOLVED halts the cell rather "
                    "than being treated as a rejection"
                ),
            },
        },
        "label_minimums": {
            "primary_set": {"allow": 100, "reject": 60, "unresolved": 20},
            "top_up_rule": (
                "If the initial primary sample misses a minimum after labeling, draw "
                "replacements from the frozen ordered reserve pool "
                "(rejudge/phase2_checker_reserve_pool_2026-07-18.json) by its pre-frozen "
                "order (world_stratified_round_robin, ascending item_id within each "
                "world's round). Verbatim rule: replacements are always drawn in the "
                "reserve pool's frozen order, never re-ordered or cherry-picked to hit a "
                "minimum faster."
            ),
        },
        "checker_gates": {
            "description": "Every candidate must pass ALL gates below to be eligible for selection.",
            "gates": [
                {
                    "id": "parseable",
                    "rule": "100% parseable exact output tokens (allow/reject/unresolved, lower-case, nothing else)",
                },
                {
                    "id": "allow_recall",
                    "rule": ">=95% exact allow on human-allow items",
                },
                {
                    "id": "reject_recall",
                    "rule": ">=95% exact reject on human-reject items",
                },
                {
                    "id": "unresolved_recall",
                    "rule": ">=90% exact unresolved on human-unresolved items",
                },
                {
                    "id": "zero_false_allow",
                    "rule": "ZERO allow outputs on any human reject or unresolved item",
                },
            ],
            "reporting": "real and synthetic subsets of the primary set are reported separately for every gate",
            "halt_rule": "If every candidate fails, halt (no model is selected; no checker call is authorized).",
        },
        "selection_rule": {
            "scope": "eligible candidates only (those passing every gate)",
            "order": [
                "1. highest macro three-class exact agreement",
                "2. higher allow recall",
                "3. lower frozen checker cost per call from the price snapshot "
                "(rejudge/phase2_provider_price_snapshot_2026-07-18.json)",
                "4. lexicographically ascending model ID",
            ],
        },
        "scoring_scope": {
            "rule": (
                "Primary validation is scored ONLY on queries that PASS the mechanical "
                "screen (rejudge.query_screen); the checker never operationally sees "
                "mechanical rejects. The mechanical-screen regression set is separate "
                "and is never used for checker-model selection."
            ),
        },
        "labeler": {
            "primary_validator": "Jack Maiorino",
            "bound_via": "rejudge/phase2_external_assignments_2026-07-18.json#/query_checker_validator",
            "second_auditor_policy": (
                "A second named human should audit every reject/unresolved label and a "
                "deterministic 20% of allows. If unavailable, the artifact records this "
                "as a single-owner normative label set rather than independent human "
                "consensus. The auditor slot is left null for now."
            ),
            "second_auditor": None,
        },
        "sampling": {
            "module": "rejudge/phase2_checker_sampling.py",
            "seed": SEED,
            "determinism": (
                "pure offline, no provider imports, no clock reads; stable sort keys "
                "and a seeded random.Random(0) for the world-stratified draw only; "
                "same-seed reruns of --build produce byte-identical output JSON"
            ),
            "universe": {
                "definition": (
                    "all exchanges with non-empty extracted_claim from "
                    "rejudge/output/records.jsonl (source stage1), joined to "
                    "data/transcripts.jsonl by question_id for candidate_a/candidate_b "
                    "text (candidate_a = correct_answer, candidate_b = wrong_answer), "
                    "plus the same join applied to every exchange in the frozen "
                    "CALIBRATION_FILES list (source calibration)"
                ),
                "calibration_files": list(CALIBRATION_FILES),
                "dedup_rule": (
                    "exact-duplicate claim texts deduped per question_id, keeping the "
                    "first occurrence encountered while iterating records.jsonl in file "
                    "order, then CALIBRATION_FILES in the fixed listed order, in line "
                    "order within each file, exchange order within each record"
                ),
                "size_after_dedup": len(built.universe),
            },
            "primary_set": {
                "path": "rejudge/phase2_checker_primary_set_2026-07-18.json",
                "n": PRIMARY_TOTAL,
                "canonical_sha256": canonical_sha256(built.primary_set),
                "composition": {
                    "world_stratified_real": {
                        "n": WORLD_STRATIFIED_PER_WORLD * 3,
                        "per_world": WORLD_STRATIFIED_PER_WORLD,
                        "selection": (
                            "for each world in sorted(WORLDS), sort that world's "
                            "mechanically-passing real items ascending by item_id, then "
                            "draw 40 via random.Random(seed=0).sample(range(pool_size), 40) "
                            "in the fixed world order (carath_norn, selvarath, "
                            "vethun_sarak); selected items are stored sorted ascending "
                            "by item_id"
                        ),
                    },
                    "boundary_real": {
                        "n": BOUNDARY_TOTAL,
                        "per_heuristic": BOUNDARY_PER_HEURISTIC,
                        "heuristics": [
                            {
                                "name": heuristic.name,
                                "description": heuristic.description,
                                "ranking_rule": heuristic.ranking_rule,
                                "selected": BOUNDARY_PER_HEURISTIC,
                            }
                            for heuristic in BOUNDARY_HEURISTICS
                        ],
                        "selection_order_note": (
                            "heuristics are applied in the fixed listed order against a "
                            "shrinking pool (items already used by the world-stratified "
                            "draw or an earlier heuristic in this list are excluded), so "
                            "no item is selected twice"
                        ),
                    },
                    "synthetic": {
                        "n": SYNTHETIC_TOTAL,
                        "pairs": SYNTHETIC_PAIR_COUNT,
                        "categories": list(SYNTHETIC_CATEGORIES),
                        "world_grounding": "world_specs/carath_norn.txt, world_specs/selvarath.txt, world_specs/vethun_sarak.txt",
                        "construction": (
                            "20 hand-authored pairs, one clearly-allowed claim and one "
                            "subtle-violation twin per pair; every claim in every pair is "
                            "verified at build time to PASS rejudge.query_screen (the "
                            "build fails closed if any authored claim does not); world "
                            "assignment is round-robin over sorted(WORLDS) giving "
                            "carath_norn=7, selvarath=7, vethun_sarak=6 pairs; category "
                            "assignment is 5 consecutive pairs per category in the fixed "
                            "category order"
                        ),
                    },
                },
            },
            "regression_set": {
                "path": "rejudge/phase2_checker_regression_set_2026-07-18.json",
                "n": REGRESSION_TOTAL,
                "canonical_sha256": canonical_sha256(built.regression_set),
                "requirement": "100% of items must be mechanically rejected by screen_query; verified at build time",
                "usage_note": "never used for checker-model selection",
                # Quoted verbatim from the regression-set artifact itself (rather than a
                # second hardcoded copy of the same prose) so the two can never drift.
                "bucketing_rule": built.regression_set["bucketing_rule"],
                "actual_counts": regression_counts,
                "frozen_design_vs_actual": built.regression_set["frozen_design_vs_actual"],
                "infeasibility_report": (
                    "INFEASIBLE AS LITERALLY STATED: the frozen design's prose calls "
                    "for 'every candidate_restatement case (8) and every "
                    "meta_or_evaluative case (18)'. Retroactively screening the "
                    "universe defined above (rejudge.phase2_checker_sampling."
                    "build_universe) finds only "
                    f"{regression_counts['candidate_restatement']} candidate_restatement "
                    f"cases and {regression_counts['meta_or_evaluative']} "
                    "meta_or_evaluative cases in total -- under every join/dedup/"
                    "reason-counting convention this implementation's author could "
                    "justify (see rejudge/phase2_checker_sampling.py module docstring "
                    "and bucket_reason() docstring for the full search). There are not "
                    "8 and 18 such real cases in this data to draw without fabricating "
                    "or double-counting items. This implementation takes ALL 7 + 13 = 20 "
                    "actually-occurring cases and fills the remaining 40 slots "
                    "proportionally from answer_or_debate_reference/compound_claim so "
                    "N=60 and 'ALL mechanically rejected' are still satisfied exactly. "
                    "Flagged here for owner review/re-freeze rather than silently "
                    "worked around."
                ),
                "design_targets": {
                    "candidate_restatement": REGRESSION_CANDIDATE_RESTATEMENT_DESIGN_TARGET,
                    "meta_or_evaluative": REGRESSION_META_OR_EVALUATIVE_DESIGN_TARGET,
                },
            },
            "reserve_pool": {
                "path": "rejudge/phase2_checker_reserve_pool_2026-07-18.json",
                "n": len(built.reserve_pool["items"]),
                "minimum_required": RESERVE_MINIMUM,
                "canonical_sha256": canonical_sha256(built.reserve_pool),
                "order": "world_stratified_round_robin",
                "order_note": built.reserve_pool["order_note"],
            },
        },
        "sources": {
            "records_path": "rejudge/output/records.jsonl",
            "transcripts_path": "data/transcripts.jsonl",
            "world_specs": [
                "world_specs/carath_norn.txt",
                "world_specs/selvarath.txt",
                "world_specs/vethun_sarak.txt",
            ],
            "oracle_query_contract": "docs/oracle-query-contract.md",
            "protocol_decision": "rejudge/phase2_protocol.json#/decisions/query_screening",
            "protocol_decision_snapshot": protocol["decisions"]["query_screening"],
        },
    }
    return design


def main() -> None:
    design = build_design()
    rendered = json.dumps(design, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    DESIGN_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {DESIGN_PATH}; canonical_sha256={canonical_sha256(design)}")


if __name__ == "__main__":
    main()
