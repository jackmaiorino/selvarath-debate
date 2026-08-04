# Phase-2 canary review

Readiness gate 12 requires this review before the main-run manifest is materialized. It is
offline analysis of the archived canary at `E:/selvarath-archive/canary-2026-07-28`. No
provider call was made. Nothing here authorizes the main run.

Inputs: 940 recorded cells, 520 reviewer rulings, 3,178 successful provider calls. Close-out
facts (accounting, chain integrity, spend, gate totals) are in
`rejudge/phase2_canary_completion_2026-08-04.json` and are not repeated here.

## 1. What the canary actually exercised

| Cell kind | n |
|---|---|
| `canary_debate_judgment` | 670 |
| `canary_no_debate_judgment` | 213 |
| `canary_debate_transcript` | 48 |
| `canary_cap_protection_judgment` | 4 |
| `canary_capped_debate_transcript` | 2 |
| `canary_full_document_judgment` | 2 |
| `canary_empty_evidence_judgment` | 1 |

890 of the 940 produced a verdict; the other 50 are transcript generation. All 940 join to a
plan cell by `cell_key`, and the three worlds are balanced (286 / 280 / 276) across 24
questions. Transcript health is clean: 50 transcripts, all 6 turns, zero word-cap violations,
zero regeneration attempts.

## 2. Two harness defects the canary surfaced

Both are in `rejudge/phase2_canary_execute.py`, both stem from the same hardcoded literal, and
both would scale unchanged into the main run.

### Defect A: single-call cells are never counterbalanced

`_run_single_call` (line 114) calls `judge_loop._format_transcript(transcript, True)`. The
second argument is `position_a_is_correct`, so the correct answer is placed at Position A in
**every** batch-replay, full-document, and empty-evidence cell. The counterbalancing function
`position_for(...)` that `judge_loop.run_judgment` uses is bypassed.

98 canary cells are affected: 95 `batch_same_qa_b2`, 2 `full_document_ceiling`, 1
`empty_evidence_table`.

Consequence: any A/B position bias in a judge is fully confounded with the batch condition.
Since batch is a phase-2 core condition (it is where the packaging finding lives), the
batch-vs-sequential contrast is the specific comparison this breaks.

These cells also omit `position_a_is_correct`, `transcript_index`, and `verdict_correct_strict`
from their result rows, because they do not route through `records.build_record`. That part is
recoverable offline: the plan is deterministic and supplies both fields, and under the correct
polarity (correct answer always at A) the observed batch accuracy is **69.5%** (66/95). No
re-run is needed to analyze them, but the confound is not repairable by analysis.

### Defect B: the query gate sees the candidate answers swapped

`_run_judgment_loop` (line 167) derives the gate's `candidate_a` / `candidate_b` with the same
hardcoded `True`, while the judge's own prompt is formatted from `position_for(...)`. Where
those disagree, the gate is handed the two answers under inverted labels.

Measured across all 332 gated cells, with no exceptions in either direction:

| | gate `candidate_a` |
|---|---|
| `position_a_is_correct` is False (224 cells) | **swapped** relative to the judge's Position A |
| `position_a_is_correct` is True (108 cells) | aligned |

So on 67% of gated cells, the frozen checker and the reviewer evaluated each proposed oracle
query against candidate labels inverted from what the judge read.

Impact is bounded but real. Most contract clauses are label-agnostic (they ask whether a query
restates a candidate answer, not which candidate it restates), and both answer texts are
present either way. The exposure is the 37 of 813 gate events (4.6%) whose query text refers to
the positions by label. Separately, every `gate_events` record in the archive carries
misleading `candidate_a` / `candidate_b` fields, which matters for anyone re-analyzing the gate
later.

Neither defect was detectable from the run's own outputs. Nothing halted, no rate moved, and
the reviewer had no way to see a swap.

## 3. Judge and condition results

Descriptive only. The canary was never powered to answer the scientific question, the
conditions have very unequal n, and defects A and B touch some of these numbers.

Accuracy by condition, strict parser, all 890 judgment cells:

| Condition | Budget | n | Accuracy |
|---|---|---|---|
| `b0` | 0 | 456 | 82.5% |
| `placebo_b2` | 2 | 166 | 85.5% |
| `sequential_b2` | 2 | 95 | 78.9% |
| `batch_same_qa_b2` | 2 | 95 | 69.5% (uncounterbalanced, defect A) |
| `clean_b2` | 2 | 71 | 95.8% |
| `capped150_b0` | 0 | 4 | 100.0% |
| `full_document_ceiling` | 0 | 2 | 0.0% |
| `empty_evidence_table` | 0 | 1 | 0.0% |

By judge, pooled: gemma-4-31B 85.9%, Llama-3.3-70B 85.9%, gpt-oss-120b 74.3%, Qwen2.5-7B 67.0%.

Two observations worth carrying into the main-run design:

- **`clean_b2` (95.8%) and `sequential_b2` (78.9%) sit on opposite sides of `b0` (82.5%)**
  despite both being clean-arm, budget-2 conditions. Whatever separates them is doing more work
  than the query budget itself. This is the content-versus-packaging question the stage-1
  results left unresolved, and the canary is too small to settle it.
- **The three descriptive ceilings went 0/3.** With n=3 that is not evidence of anything, but
  the full-document ceiling is supposed to be the easy upper bound, so a main run should not
  discover this at scale for the first time.

Parser health is good: 10 strict-parse failures in 792 comparable rows (1.3%). Nine are `b0`
cells where the judge emitted an oracle query or an empty string instead of a verdict, all from
gpt-oss-120b or gemma. Strict and pilot parsers disagree on correctness for 10 of 792 rows.

The gate's own instrumentation caught 102 checker false-allows across 813 gate events (12.5%),
which is the frozen checker's error rate as measured by the layer behind it.

## 4. Throughput: the canary was idle 86% of the time

This is the finding that governs the main-run decision.

The 940 cells span 161.9 hours (6.75 days), which is 139 cells/day. That number is misleading.
Sorting the gaps between consecutive completed cells:

- Median time per cell while running: **20 seconds**.
- Time in stretches with under 15 minutes between cells: **22.1 hours**, containing 888 of the
  940 cells. That is **40 cells/hour**, or roughly 965 cells/day sustained.
- Ten gaps longer than an hour consumed 122.9 hours, **76% of the entire calendar**.

The three largest gaps alone account for 107.6 hours (66%):

| Gap | Window (UTC) | Cause |
|---|---|---|
| 66.5 h | 07-30 00:27 to 08-01 18:56 | reviewer quota exhaustion, then waiting on the substitution and transport amendments |
| 27.1 h | 08-02 20:15 to 08-03 23:24 | halt-guard stop escalated for an owner decision |
| 14.0 h | 07-29 05:13 to 07-29 19:13 | unattended overnight stop |

None of the three was provider compute. All three were waiting on a decision or on someone
noticing. Provider degradation is visible in the data (189 gemma abandonments) but it shows up
as 5.6-hour and 3.1-hour gaps, an order of magnitude smaller than the decision-latency gaps.

**Extrapolation to 23,200 cells:**

| Basis | Rate | Elapsed |
|---|---|---|
| Observed calendar rate | 139 cells/day | 167 days |
| Active rate, if never blocked | 965 cells/day | 24 days |

Cost extrapolates cleanly and is not a constraint: $6.58 per 945 planned cells is $0.0070/cell,
so 23,200 cells is about **$162** against the $1,500 ceiling. Even tripling it for the main
run's heavier conditions leaves large headroom.

The entire question for the main run is therefore which of those two rows it lands on. The
difference is not compute, tuning, or money. It is whether routine blockers can clear without
waiting on a human.

## 5. What this review recommends before the main run

Ordered by whether it blocks.

**Blocking, science:**

1. Fix defect A. `_run_single_call` must take its polarity from `position_for(...)` like the
   judgment loop does, and must persist `position_a_is_correct` and `transcript_index` so its
   rows are self-describing.
2. Fix defect B. Derive the gate's candidates from the same formatting the judge receives.
3. Decide whether the canary's 98 affected cells are re-run, dropped, or reported with the
   confound stated. They are canary cells, so this is a reporting decision, not a data-loss one.

**Blocking, operations:**

4. Decide the supervision model. At 24 days of active time, the main run needs unattended
   operation to be the normal case. The 07-29 overnight gap and the 08-02 halt-guard stop are
   both things the supervisor could have cleared under a broader standing delegation, and the
   66-hour reviewer gap is an argument for a reviewer with headroom or a queue that does not
   stall the run.
5. Move the interpreter off `/tmp`. The run currently depends on a venv in a dead session's
   scratchpad that a WSL restart destroys. A 24-day run cannot sit on that.

**Not blocking, but owed:**

6. The three descriptive ceilings need a sanity pass before they are enumerated at scale.
7. Manifund results update.

## 6. What the canary settled

It did its job. The pipeline ran end to end at roughly 4% of main-run scale, the append-only
stores and hash chains survived three rebuild incidents, the fail-closed controls fired when
they should have, and the accounting reconciled to the cent. It also surfaced two silent
correctness defects and one throughput fact that no amount of offline testing would have found,
which is the entire reason for running it.
