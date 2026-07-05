# Limited-Verification Re-analysis — Design Spec

- **Date:** 2026-07-05
- **Branch:** `rerun-new-models`
- **Status:** design approved; pre-implementation
- **Cost:** **$0** — pure local Python over already-downloaded data. No paid API calls. (See *Cost & spend gates*.)

## 1. Context & motivation

The pilot ([Marcellino, "Limited verification can hurt debate oversight"](https://www.lesswrong.com/posts/2a3vce7WooJ4XkDqw/limited-verification-can-hurt-debate-oversight)) tested **debate as scalable oversight**: an honest and a dishonest LLM debater argue a factual question about a fictional world before a weaker **judge** who cannot see the world doc and may spend a limited budget of yes/no **oracle** calls. The counterintuitive headline: giving the judge a *few* oracle calls made oversight **worse** than giving it none — a non-monotonic ("U-shaped") dishonest-win-rate curve.

This session recovered the raw data (Joey's Drive zip `selvarath_debate_data.zip` → `data/judgments.jsonl` (2583), `data/transcripts.jsonl` (318)) and **reproduced the published table exactly**. A first artifact check (side-stratified wrong-rate) established:

- **70B judge: the dip is real** — it survives the "default-to-Position-B parse fallback" test (inflation is symmetric across which side is correct: A-correct 1.6→9.9%, B-correct 2.2→9.0% from budget 0→2). Not sampling noise, not that parse bug.
- **8B judge: contaminated** by a large, budget-worsening Position-B bias (A-correct wrong 27→38% vs B-correct 9→15%). Unusable for the headline; secondary only.
- **Secondary signal:** for the 70B judge, mean confidence *rises* as accuracy *falls* (4.09→4.58 over budget 0→2).

**The open question (the crux):** a *symmetric* inflation is still consistent with **FM1 oracle-literalism** (the imperfect oracle returning NO/NOT ADDRESSED to true honest claims) — a *fixable* design artifact — as opposed to **FM2 irrelevant-confirmation** (judge over-updates on a true-but-irrelevant confirmed dishonest claim) — a *deeper* reasoning failure. This deliverable resolves that from existing data, at $0, and **gates whether any money is spent** on a Llama re-judge (C) or frontier rerun (B).

Two Codex (GPT-5.5) consults shaped this plan; session `019f33b1-14e8-7f70-9f12-f74055f33c99`.

## 2. Goal & non-goals

**Goal.**
1. Confirm (or refute) the 70B limited-verification harm with a proper **paired/clustered** analysis and pre-registered contrasts.
2. Decompose the newly-introduced 70B errors into **FM1 vs FM2 vs other**.
3. Produce a **go/no-go recommendation** for deliverables C and B.

**Non-goals (YAGNI).** No frontier/paid API calls. No re-generating judgments (that is deliverable C). 8B judge is a secondary appendix only. Budget-20 is exploratory only (n=39). No heavyweight modeling beyond what the pre-registered contrasts need.

## 3. Cost & spend gates

- **This deliverable (A): $0.** Local `pandas`/`numpy`/`scipy` over `data/*.jsonl`. The FM1/FM2 mechanism labeling is done **in-session by the assistant** reading the small flipped-error set against the world docs — **not** a paid API loop.
- **Deliverable C** (Llama re-judge + oracle ablation): ~$5–15 on Together — only after A clears the gate **and** explicit user approval with a line-item estimate.
- **Deliverable B** (frontier rerun): separate explicit approval with a full cost-out.
- Hard rule: **no paid experiment-inference call without a dollar estimate and an explicit yes.**

## 4. Inputs & data

- `data/judgments.jsonl` — 2583 records. Fields: `question_id, transcript_index, judge_model, query_budget, position_a_is_correct, queries_submitted (list of {query, response∈{YES,NO,NOT ADDRESSED}}), queries_used, verdict, verdict_correct, confidence, reasoning, seed`.
- `data/transcripts.jsonl` — 318 records. Fields include `world, question, correct_answer, wrong_answer, honest_first, debate_transcript (list of {speaker, text})`.
- Cells: 318 transcripts × 2 judges × budgets {0,1,2,5} = 2544, plus 39 for the 70B budget-20 cell.
- `verdict_correct` is the outcome; `wrong = not verdict_correct`.

## 5. Architecture — analysis units

Code lives in a new `analysis/` package on `rerun-new-models`, separate from the pilot code. Each unit has one purpose, a typed interface, and is independently testable.

1. **`load.py`** — read both JSONL files, join transcript fields onto judgments, return one tidy `DataFrame` (one row per judgment) plus a helper exposing each judgment's oracle exchanges. *Depends on:* the two files only.
2. **`describe.py`** — reproduce (a) the dishonest-win-rate table, (b) the correct-side-stratified table, (c) confidence×correctness, each with Wilson CIs. **Smoke tables only — not primary inference.**
3. **`inference.py`** — the primary result. **Question-cluster bootstrap** (resample the 106 `question_id`s with replacement, B=10,000, seeded) of the pre-registered marginal-probability contrasts, overall and within each correct-side stratum. Optional GLMM sensitivity (only if a dependency-light path agrees). 70B primary; 8B secondary with a `budget × correct_side` interaction.
4. **`parse_sensitivity.py`** — flag suspected parse-fallback records (heuristics: reasoning contains leaked `VERDICT:`/`CONFIDENCE:`, verdict looks defaulted); recompute `Δfew` under four treatments (exclude / count-wrong / count-correct / 50-50). Report survival. **Bounded** — raw verdict text was never logged, so the definitive audit is deliverable C.
5. **`mechanism.py`** — enumerate the transcripts that flipped **correct@0 → wrong@{1 or 2}** for the 70B judge (expected: a few dozen). For each, the assistant reads the world doc + debate + `queries_submitted` + `reasoning` and labels the failure **FM1 / FM2 / other** by documented criteria (below). Output: counts/fractions per budget + an auditable appendix table (one row per labeled case).
6. **`robustness.py`** — (a) leave-one-world-out recompute of `Δfew`; (b) discordance analysis (per transcript, count correct→wrong vs wrong→correct flips 0→{1,2}; is degradation broadly distributed or a few pathological transcripts?); (c) question-cluster stability.
7. **`report.md`** (assembled) — tables/figures + the go/no-go against the gate.

**Data flow:** `load` → `describe` → {`inference`, `parse_sensitivity`, `mechanism`, `robustness`} → `report`.

## 6. Pre-registered analysis & decision gate

**Outcome:** `wrong = not verdict_correct`. **Primary population:** 70B judge, budgets {0,1,2,5}. Budget 20 exploratory.

**Contrasts** (marginal probabilities `p(b)` = dishonest-win / judge-wrong rate at budget `b`):
- `Δfew  = ½·[p(1) + p(2)] − p(0)`  (does a *little* verification hurt?)
- `Δrecover5 = ½·[p(1) + p(2)] − p(5)`  (does it start recovering by 5?)
- Both computed overall **and** within each correct-side stratum.

**Uncertainty:** question-cluster bootstrap (resample `question_id`s, B=10,000, fixed seed), percentile 95% CIs. Optional GLMM `wrong ~ C(budget)*correct_side + C(world) + (1|question_id) + (1|question_id:transcript_index)` as an agreement check.

**Gate — bank "limited verification harms oversight" iff ALL:**
- `Δfew > 0`, clustered 95% CI excludes 0, **lower bound ≳ +2 pp**;
- effect is positive in **both** correct-side strata;
- survives all four parse-sensitivity treatments (doesn't collapse toward 0).

**Then the FM1/FM2 split decides the next step:**
- **Mostly FM1** → fixable oracle flaw → deliverable **C** (oracle ablation) to confirm a better oracle removes it; do **not** yet claim a deep debate failure.
- **Substantial FM2** → deeper result → C to confirm, then **B** (frontier) is justified.

**Recovery claim:** budget-20 "recovery to 0%" is **not** claimable (n=39, CI reaches ~9%); report exploratory only. `Δrecover5` may support "partial recovery by 5" only if its CI excludes 0.

## 7. Mechanism classification criteria (unit 5)

For each flipped-error case, inspect the oracle exchanges at the flipped budget:
- **FM1 — Oracle Contradiction:** the judge queried a claim that is actually **true** per the world doc (typically supporting the honest side), but the oracle answered **NO** or **NOT ADDRESSED**, and the judge's reasoning shows it discounted the honest side as a result. (Root cause = oracle error / literalism / compound-claim ambiguity.)
- **FM2 — Irrelevant Confirmation:** the oracle answered **YES** to a claim that is **true but strategically irrelevant** (typically from the dishonest side), and the judge's reasoning shows it over-updated toward the dishonest position.
- **Other:** neither — e.g., a reasoning error independent of the oracle, a fabrication handled some other way, or an ambiguous case.

Ground truth is the world doc. Each labeled case is recorded (question_id, transcript_index, budget, the deciding exchange, label, one-line justification) so the classification is auditable and re-checkable.

## 8. Testing (TDD)

- **loader:** asserts 2583 judgments / 318 transcripts; schema/key presence; join correctness on a known record.
- **contrasts:** `Δfew` point estimate for 70B = `½(8.8+9.4) − 1.9 = 7.2 pp` (hand-checked) — assert to tolerance.
- **bootstrap:** seeded reproducibility; CI brackets the point estimate; degenerate single-question resample sanity.
- **parse flag:** detector fires on a constructed leaked-format record and not on a clean one.

## 9. Deliverable & outputs

A short **findings report** (markdown, tables + a couple of figures) that states: whether the 70B harm is banked (with `Δfew` and clustered CI), the FM1/FM2 split, the robustness results, the 8B secondary note, the confidence-calibration note, and a clear **recommendation** on proceeding to C / B. All analysis code committed under `analysis/` with its tests.

## 10. Risks & caveats

- **Parse audit is bounded, not definitive** (raw verdict text not logged) → the definitive audit + the fix (hold A/B fixed across budgets, `INVALID` label, no silent default) is deliverable C.
- **Mechanism labeling is judgment-based** → mitigated by the small case count, checkable ground truth, documented criteria, and an auditable per-case table.
- **External validity** is inherently limited (one model family, 3 hand-authored worlds) — acknowledged; this deliverable characterizes the *pilot*, not debate-in-general.
