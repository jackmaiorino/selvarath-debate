# Phase-2 Main Run: Eligibility and Spend Reconciliation

Manifest: `rejudge/phase2_main_manifest_2026-08-06c.json`. Archive: `E:\selvarath-archive\main-2026-08-06` (read-only). Generated 2026-08-11, after convergence at 22,140/22,140 billable cells.

This report was produced under analysis blinding. No outcome or verdict field (parsed verdicts, correctness flags, transcripts, judge reasoning) was read, printed, or aggregated at any point. Only cell keys, condition/kind/model fields, gate decision metadata, completion status, sequence numbers, and cost fields were used, via an explicit field whitelist enforced at parse time. Full machine-readable detail is in `eligibility_report.json`.

## 1. Spend reconciliation

Computed the way the pipeline itself computes it: `rejudge.api_client.load_chained_usage_ledger` over the live `main_usage.jsonl` (165,818 events, hash-chain verified), bound with `rejudge.phase2_canary_live.carried_forward_spend` reading `canary_ledger_binding.json`'s carry-forward from the retired predecessor ledger. This is the exact composition `phase2_canary_live.py` uses to seed the live client's `initial_spend_usd` / `initial_uncertain_spend_usd`.

| Component | Actual (settled) | Uncertain |
|---|---:|---:|
| Live ledger (`main_usage.jsonl`) | $137.11995 | $7.70977 |
| Carried forward (retired predecessor) | $27.38350 | $0.64636 |
| **Total** | **$164.50** | **$8.36** |

**Total accounted spend: $172.86** (settled + uncertain).

- vs stage cap $400.00: within cap, $227.14 headroom (43% of cap used).
- vs the $173.80 projection in `phase2_main_authorization_basis_2026-08-06b.json`: $0.94 under (-0.5%). The bridge-canary per-cell estimate ($0.00572 settled + $0.00213 uncertain per cell) held closely over the full main grid.
- Cross-check against `rejudge.phase2_canary_live.conservative_ledger_spend` (chain-agnostic re-derivation): matches the strict hash-chained summary exactly ($137.11995 actual / $7.70977 uncertain).
- Cross-check against the supervisor's last printed uncertain figure: `orchestrator.log` last printed "uncertain $7.637" at the final benign-transient resume. Recomputing the same live-ledger-only formula (`scripts/canary_supervisor.py`'s `_uncertain_spend`) against the final ledger gives $7.710, a $0.073 difference. That print was mid-run; the small gap is consistent with a handful of reservations that stayed unterminated after that point without triggering another benign-transient retry, resolving only at final convergence. There are 12 unmatched (still-reserved) events in the final live ledger.
- No cap breach at any point; the run converged with substantial headroom under both the stage cap and the pre-registered forecast.

## 2. Completion census by condition

Planned counts come from `rejudge.phase2_main_manifest.enumerate_main_cells` / `billable_cells` (deterministic re-derivation from the frozen protocol and question set, independent of the archive). Cell keys are `{namespace}:{kind}:{sha256}`; `kind` is the second segment, `condition` is a plan field (present for every billable cell, including transcript cells, which do not carry their own `condition` field in the result row).

**22,140 / 22,140 billable cells complete (100%).** No missing cell keys, no unexpected cell keys, no duplicate cell keys.

| kind | condition | planned | completed |
|---|---|---:|---:|
| debate_transcript | blind_uncapped_3_round | 492 | 492 |
| capped_debate_transcript | blind_capped150_3_round | 492 | 492 |
| debate_judgment | b0 | 3,936 | 3,936 |
| debate_judgment | sequential_b2 | 3,936 | 3,936 |
| debate_judgment | batch_same_qa_b2 | 3,936 | 3,936 |
| debate_judgment | placebo_b2 | 3,936 | 3,936 |
| cap_protection_judgment | capped150_b0 | 984 | 984 |
| empty_evidence_judgment | empty_evidence_table | 492 | 492 |
| full_document_judgment | full_document_ceiling | 984 | 984 |
| no_debate_judgment | b0 | 984 | 984 |
| no_debate_judgment | clean_b2 | 984 | 984 |
| no_debate_judgment | placebo_b2 | 984 | 984 |

Every row matches planned = completed exactly. (The 1,060 capability-QA cells ran under a separate manifest and are correctly absent from this file: 22,140 result rows total, matching the billable count exactly.)

## 3. Binding differential-incompletion gate

Gate source: `rejudge/phase2_missing_data_policy_proposal_2026-08-04.json`, ceiling 0.5 percentage points between the highest and lowest completion rate among the three primary debate-grid arms.

| Arm | Planned | Completed | Completion rate |
|---|---:|---:|---:|
| b0 | 3,936 | 3,936 | 100.0% |
| sequential_b2 | 3,936 | 3,936 | 100.0% |
| batch_same_qa_b2 | 3,936 | 3,936 | 100.0% |

**Max pairwise completion-rate difference: 0.0pp.** Gate passes trivially, verified rather than assumed: all three arms are individually confirmed at 100% completion above, not inferred from the aggregate 22,140 total.

## 4. Edge-case ledger

### 4a. Malformed gate dispositions

Four payload hashes are recorded as "malformed" (self-contradictory reviewer output: LABEL REJECT with CLAUSE Allowed, which the frozen parser accepts but the decision store commits as status `malformed` with null label/clause) across two incidents:

- `e2a1dbc57b5b...` and `4ef8b152aa71...`: `rejudge/phase2_main_contract_gap_2026-08-09.json` (empty-query shape), decision-store sequence 9442-9443.
- `4fa1fcd96195...` and `346e412b5a41...`: `phase2_main_contract_gap_occurrence3_2026-08-10.json` / `occurrence4_2026-08-11.json` (open-question shape), decision-store sequence 10420 and 12490.

A malformed disposition is non-ALLOW, so the query is blocked, not dispatched. This is a valid in-protocol outcome under the frozen failure rule, not a missing-data exclusion.

Searching every completed cell's `gate_events` for these four payload hashes, cross-checked with an independent full-text substring scan of all 22,140 result rows:

| Payload | Cell key (short) | Condition | Question | Judge |
|---|---|---|---|---|
| `4fa1fcd96195...` | `...cb88a2b6...ebfed` | placebo_b2 | SEL-026 | Qwen2.5-7B-Instruct-Turbo |
| `346e412b5a41...` | `...eeed9576...76bc6` | sequential_b2 | SEL-026 | Qwen2.5-7B-Instruct-Turbo |

The other two hashes (`e2a1dbc57b5b...`, `4ef8b152aa71...`) do not appear in any of the 22,140 converged result rows, by either the structural `gate_events` scan or the substring scan (0 occurrences each). This is confirmed, not a parsing gap. Best-supported explanation given the timeline: both were committed before the 2026-08-09T22:55 incident-5 decision-store rebuild and the 2026-08-10 in-flight cache-drop sweep (1,346 cells). A cache-drop forces a fresh judge sample rather than a byte-identical transport retry, so whichever cell(s) originally raised these two queries most likely resampled a non-empty query on re-derivation and never referenced these hashes again. The decision store retains both rulings permanently regardless (it has no cell linkage by design). This is stated as inference: no data structure ties a decision-store payload to the specific cell that first raised it, so the originating cell(s) cannot be identified directly.

### 4b. Incident-5 re-run cells (1,070)

Reused the existing, already-computed `analysis_out/contamination_closure.json` (783 directly-exposed + 287 dependent cells = 1,070 total drop set, computed by the read-only `scripts/compute_contamination_closure.py` against this same archive pre-convergence). All 1,070 of those cell keys are present in the converged 22,140-row result set. Per `rejudge/phase2_main_incident5_replay_2026-08-09.json`, these cells were invalidated (their cached calls consumed a fabricated REVIEWER_UNAVAILABLE ruling from a 2026-08-07 reviewer outage), had their cached calls dropped, and were re-run against the corrected, re-reviewed decision store. **They are ordinary eligible cells in the converged run**, per the predeclared disposition, not an exclusion category.

### 4c. Anomalous status fields

Fields relied on for this scan: `dry_run`, `cell_key` (duplicate check), and within `gate_events`: `halted`, `reviewer_status`, `action`, plus `harness_version` / `parser_version` for version homogeneity.

- `dry_run = True`: none found (0 of 22,140).
- Duplicate cell keys: none found.
- Gate events with `halted = True`: none found (consistent with the driver raising before recording any halted event, so a persisted halted event should never occur).
- `reviewer_status` values observed: `parsed` (25,179), `malformed` (2). No unrecognized values.
- `action` values observed: `allow` (15,220), `block` (4,026), `retry` (5,935). No unrecognized values.
- `parser_version`: single value `2.0.0` throughout, homogeneous.
- `harness_version`: 13 distinct short-commit values across the run. This is not a correctness anomaly: the run spanned 2026-08-06 to 2026-08-11 with multiple documented mid-run fixes (reservation-overrun fix, auto-resume amendments 10-14, incident-5 remediation, contract-gap dispositions), each of which would bump this field. Flagged here for the record since it is the one field that varies across the run's own metadata, not because it indicates a defect.

No structural anomaly (duplicate rows, dry-run contamination, unknown gate statuses, or persisted halts) was found anywhere in the 22,140-row converged result set.
