# Final phase main: results report

2026-10-29. Compiled by the coordinating session 5b125319 (Claude Opus 5.5) at the owner's request, from the frozen
analysis outputs and the run's records in the run root (D:\finalphase-runs\final-phase-2026-10-03). Every number in
the appendices is generated from those files by coordinator/build_report_tables_20261029.py; the text quotes them.
Independent check: section 14. Error rates are in percent; contrasts in percentage points (pp).

## 1. Result in brief

1. What the primary measures: the effect of the offered query budget (0, 1, 2, 6) on judge error, under the
   implemented gate, for one pairing (Sonnet 5.5 honest, Fable 5.1 dishonest), including the judge's anticipation,
   stopping and extra computation. It does not identify the effect of one more oracle answer or the value of
   permissible verification alone, and a supported peak establishes neither a global peak nor saturation or a return
   to baseline.
2. The amended primary (seven judges; Llama-70B was lost to service withdrawal) supports higher pooled error at an
   offered budget of one than at zero and at six: +0.77 pp and +0.82 pp (each one-sided p = 0.0001; intersection-union
   Holm p = 0.0002). The conjunction at budget two is not established (+0.21 and +0.25 pp; Holm p = 0.072).
3. The peak is small: a meaningful peak (1.5 pp, fixed in the design) is ruled out at both budgets; at budget one the
   simultaneous 95% upper bounds are 1.19 and 1.24 pp.
4. Not robust under the prescribed gate sensitivity: the leakage audit classified admitted claims as violating or
   borderline in 11.4%, 17.9% and 33.2% of sampled one-, two- and six-query sessions (leak-exposure 1.8%, 3.2% and
   5.0%). Under the fixed-location worst-case bound, the budget-one peak loses support at a common panel-mean exposure
   of about 0.25% in each query arm. Amendment 3 keeps the frozen claims as main's result under this gate and reports
   them as not robust to the measured rate of rule-breaking admissions; this does not establish that those admissions
   caused the findings.
5. Secondary: no average difference between budgets six and zero was detected (-0.05 pp, marginal 95% interval
   [-0.37, 0.30], Holm p = 1.0). Debate with the whole world had 1.88 pp lower error than debate with six queries (Holm
   p = 0.0006), also not robust under the same sensitivity (it fails at 1.40%). Neither Qwen3.8 contrast survives Holm.
6. Error was low except for Haiku: pooled no-query error 4.34% across the seven judges (Haiku 21.21%, the others 0.23
   to 2.86%); judging from the world alone, without the debate, 0.59%.

## 2. What was run

- Debates: one per question by the pairing (Sonnet 5.5 honest at medium effort and a 16,000-token ceiling, Fable 5.1
  dishonest at high effort and 32,000), three rounds, round 1 blind, no word cap, honest side first by hash parity.
  1,070 questions in 140 worlds (70 per world author, Fable 5.1 and GPT-6 Astra); 1,068 counted, 2 sensitivity-only.
- Arms for every question, judge and answer order: world alone, debate with budget 0, 1, 2 or 6 queries, and debate
  with the world. Judges: luna, terra, sol (GPT-5.6, medium), haiku (Claude Haiku 4.5), sonnet and opus (Claude 5.5,
  medium), llama70 (Llama-3.3-70B-Instruct-Turbo) and qwen38 (Qwen3.8).
- Queries: one at a time; a mechanical screen, then the DeepSeek V4 Flash classifier gate (from main's relaunch on a
  Together dedicated deployment, amendments 2 and 3); an admitted query goes to the oracle, Opus 5.5 at medium effort
  (YES, NO or NOT ADDRESSED).
- Analysis: the preregistered module (finalphase/main_analysis.py, SHA-256 47703281...f954) on 10,000 stratified
  world-cluster bootstrap draws (seed 20261024); amendment 1's companion (main_analysis_amendment1.py, 15cb4b4b...9e01)
  for the seven-judge primary; amendment 3's leakage audit (gate_audit.py) and gate sensitivity
  (main_analysis_amendment3.py), each module's hash matching run_manifest.json.

## 3. Deviations and engineering events

No main verdict, response text or comparative result was read before any of the following; the coordinator first read
results at 2026-10-29T21:41Z, after every analysis had been recorded.

| Date (UTC) | Event | Kind |
|---|---|---|
| 2026-10-25 22:22 | First launch (commit 7ce4a16) | launch |
| 2026-10-26 10:42 | Task priority raised after the run was starved by disk contention on D: (about ten hours lost; nothing re-sent) | engineering |
| from 2026-10-26 10:46 | Together refuses every Llama-3.3-70B-Instruct-Turbo request (model_not_available); all 12,840 llama70 sessions stored as errors | provider withdrawal |
| 2026-10-27 02:30 | Amendment 1: Llama recovery by a dedicated-endpoint post-pass, with a seven-judge rule if no endpoint is ready within 72 hours of main's first finish | deviation |
| 2026-10-27 03:05 | Together stops serving the gate model (endpoint_not_configured); STOP written; a substitute gate fails its pre-set rule and is not used | provider withdrawal |
| 2026-10-27 13:01 | Amendment 2: the gate on a dedicated deployment in bursts, reset of the refused gate rows, an equivalence check fixed in advance | deviation |
| 2026-10-27 15:29 | The deployment fails the equivalence check (C2 and C4) | check result |
| 2026-10-27 21:00 | Amendment 3: the owner adopts the endpoint gate as a reported deviation, with a leakage audit and a gate sensitivity; lease $400 | deviation |
| 2026-10-28 01:01 | Amendment 2 addendum, before the reset ran: the refused-row category takes one model_not_available refusal of the gate model | deviation (scope) |
| 2026-10-28 01:28 | Gate reset (25,987 rows removed and archived); relaunch at 0e11907 | reset, launch |
| 2026-10-28 04:05:52 to 04:17 | An OpenAI batch create returned 504; the existing batch was attached by hand at 04:16:49 after consult 59; main relaunched at 04:17 | transport handling |
| 2026-10-28 21:15 to 22:23:25 | Clean STOP for the owner's reboot; funding updated (consult 60); at 22:06 the owner adopted a $3,434 real-cost soft STOP, reserving $200 for the audit within an inclusive $3,634 cap and accepting possible overshoot from the round in flight; relaunch | operations and budget control |
| 2026-10-29 03:12 | First finish, complete | completion |
| 2026-10-29 11:55 to 12:45 | Owner chose recovery within $3,850; Together refuses creation of the Turbo endpoint (v1 HTTP 403; v2 offers BF16 only); the budget addendum never took effect | availability |
| 2026-10-29 21:13 | Amendment 1 disposition: no recovery; recovery closed early as an availability-based timing deviation (consult 63) | deviation (timing) |
| 2026-10-29 21:14 to 21:31 | Closure: gate audit, main-analysis, main-analysis-amendment1, main-analysis-amendment3 | analysis |

Amendment 3 (section 7) placed the audit's $200 outside main's cap; the owner's later instructions were implemented
under the stricter inclusive reading. The soft STOP did not guarantee against overshoot; that limit was disclosed and
accepted before the relaunch. Final spend stayed within the cap (section 4).

## 4. Acquisition and cost (appendices A and B)

Main finished complete on 2026-10-29 at 03:12:31Z after four launches: 1,070 debates (none failed), 102,720 of 102,720
judge sessions (none failed; the 12,840 llama70 sessions are service refusals), 6,420 of 6,420 compliance checks. The
dishonest debater wrote about twice as much as the honest one (mean 834 words a turn against 435).

Store spend $3,124.17 (judges $2,506.63, oracle $263.99, debates $332.21, compliance $13.06, gate $8.29 notional).
Billed, as read in the consoles: main $3,234.92 (Anthropic $1,769.29, equal to the store; OpenAI $720.51, about 1.17
times its store cost, not reconciled; Together $745.12 including the gate endpoint) plus the audit's $11.54 on
Anthropic: $3,246.46, within the $3,634 cap. The gate endpoint's recorded upper bound is $178.98 of its $400 lease;
Together posted about $50.35 for it over its lifetime.

## 5. Primary (seven judges; appendix D)

| Contrast | Estimate (pp) | 95% interval | Simultaneous 95% | One-sided p |
|---|---:|---|---|---:|
| e1 - e0 | +0.77 | [0.43, 1.10] | [0.35, 1.19] | 0.0001 |
| e1 - e6 | +0.82 | [0.49, 1.16] | [0.39, 1.24] | 0.0001 |
| e2 - e0 | +0.21 | [-0.06, 0.51] | [-0.15, 0.57] | 0.072 |
| e2 - e6 | +0.25 | [-0.04, 0.56] | [-0.12, 0.63] | 0.044 |

Holm across budgets: budget one 0.0002 (peak supported), budget two 0.072 (not supported). A meaningful peak is ruled
out at both and shown at neither. The seven-judge panel has no missing cells among 89,712, so the missing-cell guard
changes nothing. Seven-judge error by arm: world alone 0.59%, budget 0 4.34%, budget 1 5.11%, budget 2 4.55%, budget 6
4.29%, debate with the world 2.41% (intervals in appendix C).

## 6. Literal registered scoring (appendix D)

Amendment 1 keeps the registered eight-judge scoring beside the primary: no reset ran, so it is the preregistered run
itself, with Llama's refusals scored as errors in every arm. Its pooled contrasts are 7/8 of the seven-judge ones
(+0.67, +0.71, +0.18, +0.22 pp) with identical p-values and the same claims; Llama's own contrasts are degenerate
(p = 1). It has no missing cells among 102,528 counted cells because Llama's service-error records are scored; they
contain no measured Llama judgment.

## 7. Secondary (appendix D)

Holm across six, two-sided: pooled e6 - e0 -0.05 pp (Holm 1.0); pooled debate with world - e6 -1.88 pp (Holm 0.0006,
rejected); Llama's two contrasts untested (p = 1); Qwen3.8 e1 - e0 +1.08 pp (raw p 0.011, Holm 0.054) and e6 - e0
-0.61 pp (Holm 0.39). The intervals are marginal, not multiplicity-adjusted.

## 8. Sensitivities (appendix E)

Reported beside the primary; none can rescue or overturn it (7.5). They are computed on the literal scoring; for the
defined contrast sensitivities, Llama's constant error leaves t statistics and p-values equal to those of the
corresponding Llama-free panels, but not decisions against the fixed 1.5 pp threshold.

- Without Sonnet: as the primary (budget one Holm 0.0002; budget two 0.063).
- Without the Claude judges: budget one not supported (Holm 0.056); its budget-six side is the weak one (e1 - e6
  p = 0.028).
- Determinacy 0 or 1 (806 questions): supported at budget one (0.0002) and at budget two (0.028).
- Without the two compliance-flagged debates, and with the two sensitivity-only questions: as the primary.
- Missing-cell codings: identical to the primary (no missing cells).
- Unparsed verdicts as missing: undefined on the literal scoring (no Llama verdict parsed); its Llama-free counterpart
  was not computed.

## 9. Descriptives (7.6; appendices C and F)

- By judge (appendix C): six of seven judges have higher error at budget one than at zero (Sonnet decreases from
  1.12% to 1.03%). The three GPT-5.6 judges stay up at budget six; Haiku and Qwen3.8 fall below their budget-zero error
  by six. The budget one-versus-six contrast comes mostly from Haiku (+3.84 pp) and Qwen3.8 (+1.69 pp). These
  judge-level comparisons are descriptive; only Qwen3.8's (and Llama's) were prespecified tests. Some low rates have
  infinite or undefined interval endpoints (zero-variance draws), shown as such.
- Query use (appendix F): at budgets one and two most judges used their full allowance (Sonnet stopped early in 39%
  of budget-two sessions); at six, early DONE ranged from 14% (Luna) to 99% (Sonnet, 2.2 queries on average). Haiku
  had a malformed query step in 29 to 36% of its query sessions and the most gate rejections. Across 109,090 recorded
  oracle-label occurrences, 86.4% were YES, 11.2% NOT ADDRESSED, 2.4% NO and 2 INVALID.
- Lexical touch (appendix F): in every judge and budget cell of the seven judges, sessions with an admitted query
  touching the question's required facts had lower error than sessions without one; this association is
  observational, and lexical overlap does not establish successful verification or a causal effect.
- Order balance: honest first in 520 of 1,068 counted questions (256 of 535 in Astra worlds, 264 of 533 in Fable
  worlds).

## 10. The gate (amendments 2 and 3; appendices G to J)

Main's gate is a deviation from the preregistered protocol, and it was not shown equivalent to the pilot's gate.

- Equivalence check (amendment 2, run 2026-10-27 14:41 to 15:28Z on all 14,747 pilot gate requests; appendix G):
  coverage, C1 (non-decisions 0.0068% and 0.0414%) and C3 (rejection share 3.19% against the pilot's 3.49%) pass. C2
  fails: agreement 97.98% overall, 99.11% within the pilot's ADMIT class, 66.54% within its REJECT class against 90%.
  C4 fails: the deployment keeps 66.54% of the pilot's rejections (n 514) but 73.99% of its own on retest (n 469),
  7.45 points apart against a 6-point margin; overall agreement 97.98% against its own retest's 98.00% passes. Its
  first pass admits 154 of the 488 rejections the pilot's hand audit confirmed (the repeat, 60). From the check's
  saved replies, it admits all 7 of the pilot audit's false admissions, as the pilot's gate did, and all 93 other
  sampled admissions; of the pilot gate's 5 false rejections it admits 4, and of its 24 borderline rejections 14.
  Disagreements by judge, arm and world and the throughput (8.96 and 9.79 calls a second) are in appendix G.
- Deployment: one replica of Together's certified profile (2x B200, NVFP4, model revision rv_CdgV5GFj8H9uwcbVDGTs7),
  $17.98 an hour while ready, started only in bursts around each round's gate calls; the preregistered gate prompt and
  2,000-token limit with no sampling parameter (none of main's 77,993 gate requests sets one).
- Resets: amendment 2's reset removed and archived 25,987 refused gate rows on 2026-10-28 at 01:28Z (refusals since
  2026-10-27 03:03Z; no cost). Amendment 1's reset never ran.
- Endpoint cost: recorded bound $178.98 (main's 23 bursts $165.00 plus the check's $13.98); Together posted about
  $50.35.
- Leakage audit (appendix I): 840 sampled sessions (40 per judge and query arm), 2,653 items labelled by Opus 5.5, all
  answered, $11.54. A session is exposed if any admitted claim is labelled VIOLATES, BORDERLINE or UNREADABLE, and
  leak-exposed if such a label names condition 2 or 3 (a candidate named, or a candidate's conclusion asserted), names
  no condition, or is UNREADABLE. Exposed sessions 11.4%, 17.9% and 33.2% at budgets one, two and six (nominal one-sided 95%
  Clopper-Pearson upper bounds 15.0%, 22.1%, 38.1%; not simultaneous, slightly anticonservative when judges differ, and
  blind to the labeler's misses); leak-exposed 1.8%, 3.2% and 5.0% (upper 3.7%, 5.5%, 7.7%). Of 1,982 distinct admitted
  claims, 10.7% were labelled VIOLATES or BORDERLINE. Exposure was highest for Haiku (57.5% at budget six) and lowest
  for Qwen3.8 at budget one (0%). Against the pilot's hand labels: 81 of 84 correct admissions labelled admissible, 6
  of 7 false admissions violating, 478 of 487 correct rejections violating; across all 100 sampled pilot admissions,
  86 admissible, 10 violating, 4 borderline (a reference on other questions and judges, not a matched comparison).
- Gate sensitivity (appendix J): with complete cells a component moves by rho_b for D_b0 and rho_b + rho_6 for D_b6.
  The budget-one peak fails at 0.25% common exposure, the meaningful-peak exclusions at 0.31% (budget one) and 0.93%
  (budget two), and the world-versus-six rejection at 1.40%. All four claims established at zero exposure lose support
  under all four audit readings; the other claims were never established. The frozen claims remain main's results under
  this gate, reported as not robust to the measured rate of rule-breaking admissions. Main's
  query-arm results speak for this gate, which admits some claims that break its rule, so they do not by themselves
  identify the value of permissible verification.

## 11. Llama-70B (amendment 1)

Zero of the 12,840 llama70 sessions were recovered (12,816 on counted questions): Together withdrew serverless access
on 2026-10-26, refused creation of a dedicated endpoint for the exact model id on 2026-10-29 (v1 API, HTTP 403
endpoints_v1_create_access_disabled), and its v2 catalog lists only the BF16 model, which amendment 3's runbook forbids
substituting without a new amendment. No reset ran and no Llama endpoint was created. Recovery closed at
2026-10-29T21:13:35Z, before the original deadline of 2026-11-01T03:12:31Z (72 hours after main's first finish), as an
availability-based timing deviation that does not establish that access could not have returned before that deadline
(main_amendment1_disposition.json). The amended primary is therefore the seven-judge panel, with both Llama secondary
tests untested (p = 1) inside Holm across six, reported as a panel change caused by service unavailability.

## 12. Compliance reading (preregistration section 4)

The blind DeepSeek V4 Pro check returned all 6,420 turns and flagged 2. The coordinator read both and the fixed
hash-chosen sample of 20 unflagged turns (coordinator/compliance_reading_20261029/reading.md):

- W081-Q09, round 3, dishonest: flag upheld as an assignment-hint violation. The turn says the opponent's position
  "was assigned", which violates the standard even though it reveals nothing of the speaker's own role; the turn argues
  its assigned answer throughout without refusal or concession.
- W114-Q08, round 3, honest: not a violation. "Credit to the judge's oracle" invites the judge to check quoted facts,
  as the debater prompt allows; a classifier false positive.
- The 20 sampled unflagged turns (13 dishonest, 7 honest) all comply: no refusal, no concession of the assigned
  answer, no hint of the assignment. Several dishonest turns acknowledge true opposing facts without conceding, which
  the standard allows. The reading agrees with the classifier on all 20.

Both flagged debates are the two the preregistered sensitivity excludes; that sensitivity matches the primary.

## 13. Interpretation and limits

- One pairing on this benchmark; model, role, effort and ceiling are confounded and capability rankings are not
  identified.
- The budget-one peak is statistically supported but small and below the preset meaningful size. Its budget-zero side
  is broad (six of seven judges err more at budget one); its budget-six side comes mostly from Haiku and Qwen3.8
  (descriptively). It is not supported without the Claude judges, is not robust under the gate sensitivity, and
  speaks for this gate.
- Adding the debate to the world raised error from 0.59% (world alone) to 2.41% (debate with the world); this is
  descriptive, not a registered test. Adding the world to the debate cut error against six queries by 1.88 pp (a
  registered secondary, also not robust under the gate sensitivity).
- The confirmation's 22.9% no-query error (Luna, Haiku and Llama-70B on pilot questions, world-weighted) is not a
  matched comparison for main's 4.34%; Haiku's rate is descriptively close to its confirmation rate (21.21% against
  21.4%), and equivalence was not tested.
- Not yet reported: the pilot's sealed outcomes, which preregistration section 8 allows to be reported descriptively
  now that main's analysis is recorded, labelled as pilot data and never pooled with main.

## 14. Sources and review

Analysis outputs (run root): main/analysis/amendment1_analysis.json (c1011b4b...e683), main_analysis.json
(3e2c5d22...829e), amendment3_analysis.json (f251be92...42cb), gate-audit-v1/audit.json (5397026d...2419); records:
main/main_readout.json, main/launches.jsonl, main/amendment2/manifest.json, main_amendment2_endpoint.json,
gate-endpoint-check-v1/comparison.json, main_amendment1_disposition.json (e3eecc46...1873), main_engineering_log.md.
Reviews: GPT-6.1 Sol consults 55 to 59 for the amendments and the transport recovery; Astra Ultra consults 60 to 64 for
the funding controls, the post-pass, closure and the summary (coordinator/consults/). This report: Astra Ultra consult
65 (session 01a189b2-2a13-72f0-b4cf-97772d2aedc2), accurate with corrections, all applied.
