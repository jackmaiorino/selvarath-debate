# Restoring k0 difficulty: probe results and the next two candidates (2026-10-18)

Status: prepared, nothing run. Both candidates need Jack's go before any spend.

## Record: the stop rule was met

The canary (2026-10-10, `canary-preparation.md`) gave pooled uncapped k0 error 2.9% for the
canary panel, under the 10% floor for both families, so the design's rule stopped the study
before the pilot. Everything since is bounded development under Jack and Joey's direction;
the frozen protocol has not resumed, and no pilot or main request has been made.

## Harder-question probes (recorded here for the first time)

Each probe authors 8 fresh H worlds (4 per author) in its own directory and store under
`D:/finalphase-runs/final-phase-2026-10-03/`, validates them with the frozen rule, debates 20
retained questions (10 per author, fixed hash order) in Fable and Astra self-play uncapped,
and judges at k0 with the canary panel in both orders (240 judgments).

| Round | Author block | Retained | Pooled k0 | Fable | Astra | Spend |
|---|---|---:|---:|---:|---:|---:|
| 1, 2026-10-11/12 | hardness-v1 (buried decisive detail, salient wrong side, near-miss detail) | 88/96 | 5.0% (12/240) | 7.5% | 2.5% | $52.29 |
| 2, 2026-10-13 | hardness-v1 + v2 (prior traps) | 83/96 | 2.9% (7/240) | 5.0% | 0.8% | $50.06 |

By judge: round 1 Luna 3.8%, Haiku 3.8%, Llama 7.5%; round 2 Luna 0%, Haiku 5.0%, Llama 3.8%.
Errors are concentrated: 6 of 20 questions drew any error in round 1 (at most 3 of 12), 3 of
20 in round 2 (H011-Q11 4/12 with 4 determinacy flags). In round 2, errors were associated
with determinacy flags: 10% on questions with 2+ flags against 0.6% with none. Summaries: `probe-hardness-v1/canary/summary.json`, `probe-hardness-v2/canary/summary.json`.

Note on the Phase 2 comparison: of the 12 Phase 2 questions re-debated on 2026-10-11, 3 are
validator-clean with both validator pairs, 1 (VS-025) with one pair only, and 8 with neither.
Validator-clean is not full pipeline eligibility: VS-015 supplies 8 of the 10 errors in the
clean three and fails today's 1.25 length check, and the other two drew 2 of 24. So the
subset is not evidence that the full pipeline can keep difficulty.

## Direction (Joey, relayed by Jack 2026-10-18)

Harder questions are the cleanest fix to the extent possible; asymmetric debaters are a
defensible fallback. Try a bit more with harder questions, leaning towards asymmetry.

## Guidance consult

Astra Ultra reviewed both candidates (`consults/2026-10-18-01-guidance.md`) and then the
code and this note (`consults/2026-10-18-02-review-*.md`). Adopted: drop
the prior-trap block; a factual-crux v3 that changes an inference rather than answering a
lookup; author self-audit separating factual support from inferential sufficiency, inside the
existing private ledger (no new rejection criterion, so the retention rule is unchanged);
keep all six task types; run exactly the two model pairings below; pre-declared screen
below; report by question, world, judge and order plus valid-verdict error. Its non-signoffs
are recorded in the consult and carried into the reading below.

## Candidate A: hardness-v3 (round 3)

`AUTHOR_HARDNESS_V3_AMENDMENT` in `finalphase/prompts_authoring.py` replaces both earlier
hardness blocks (Fable keeps its length amendment). Worlds H017-H024 reuse round 1's seed
hints and author settings, so the two rounds have matched author configurations; their worlds
are still fresh generations, retained independently, so they do not differ only in the
block. Same probe steps and caps as rounds 1 and
2 (author+validate $30 cap, debates+judging $45 cap; about $50 expected):

    python -m finalphase.cli probe-author --revision hardness-v3
    python -m finalphase.cli probe-validate --revision hardness-v3
    python -m finalphase.cli probe-canary --revision hardness-v3

## Candidate B: asymmetric debaters

`debate_task` takes an optional dishonest debater; self-play ids and records are unchanged.
`probe-asym` (`finalphase/asym.py`) re-debates the 20 canary benchmark questions uncapped
with `haiku` (claude-haiku-4-5) honest against `fable` (claude-fable-5-1) dishonest and
`luna` (gpt-5.6-luna) honest against `astra` (gpt-6-astra) dishonest, as mapped in
`finalphase/models.py`: each frontier model against a lower-priced model of its own provider.
It runs the canary's DeepSeek compliance check on every turn and judges at k0 with the canary
panel in both orders (40 debates, 240 judgments). Before any request it checks that the saved
self-play canary judgments cover every matched cell (question, dishonest model, judge, order)
exactly once and refuses to start otherwise; each pairing is then compared with its own
family's self-play on matched cells. Own directory `probe-asym-v1`, own store, $40 cap; about
$20 expected. Effort asymmetry (`fable@low~fable`) is supported but not run.

    python -m finalphase.cli probe-asym

Reusing the canary questions is a paired exploratory comparison: it holds question
difficulty fixed, but those questions already decided the stop, so a pass is not
confirmation.

## Pre-declared reading (both candidates)

The screen is per family (A) or per pairing (B), 120 judgments each, with the frozen scoring
(unparsed verdicts count wrong):

| Result | Decision |
|---|---|
| Any declared cell missing, duplicated or undeclared | Incomplete: no reading; finish or rerun the missing cells |
| Every group at least 12/120 | Screen pass (then the eligibility rule below for B) |
| Every group at most 11/120 | Screen fail: stop this candidate |
| Otherwise | Mixed: not enough for two-family adoption; a one-family study needs Jack and Joey's explicit decision |

A screen pass for A, or for B a pass where every pairing is also confirmation-eligible, leads
to freezing that revision and confirming on fresh worlds selected by retention alone.

Confirmation eligibility for B, per pairing, pre-declared: the raw cell is at least 12/120;
at most 2 of 20 honest debates have a compliance violation (the design's canary limit); no
honest debate has an unknown compliance result (missing or malformed check); and error on the
honest-compliant debates is still at least 10%. Raw scoring stays frozen. The
honest-compliant figure is a sensitivity analysis (post-treatment selection), reported with
its denominator and the self-play error on the same questions, never in place of the raw
result. A debate is honest-compliant only if every honest turn has a valid negative check.

This is stronger than the frozen stop rule (stop only if every family is under 10%), on
purpose: a combined 24/240 can hide one unusable family. It is a development screen, not
statistical certification: 20 questions over few worlds are not 240 independent
observations, and a crossing concentrated in one world or one question is fragile. Each
summary reports error by question, world, judge and order, valid-verdict error and the
invalid-verdict rate; malformed verdicts are not read as persuasive difficulty. For B,
`pairings_detail` reports each pairing's compliance counts, raw and valid-verdict error,
matched self-play error, the honest-compliant sensitivity and eligibility.

If both pass, prefer A (symmetric frontier debaters) and keep asymmetry as a separately
reported robustness condition rather than combining the fixes. Asymmetry keeps the
query-budget comparison causal when held fixed across arms but changes what it means:
verification may compensate for a weak honest side, and a stronger dishonest side may change
what judges query. Neither result is evidence for the small-budget peak; the frozen contrasts
decide that.

## Results, 2026-10-18 (approved in the project thread; run on the desktop at d1819fd)

All four steps exited 0 with no retries and no parse failures. Spend $72.13 (exact; the rounded parts sum to $72.14): probe-asym
$18.89 ($40 cap), hardness-v3 author and validate $20.62 ($30 cap), hardness-v3 debates and
judging $32.63 ($45 cap). Returned model ids matched the registry for every call
(gpt-6-astra, claude-fable-5-1, claude-haiku-4-5, gpt-5.6-luna, Llama-3.3-70B-Instruct-Turbo,
DeepSeek-V4-Pro-0813).

**B, asymmetric debaters: mixed.** Complete grid, every honest and dishonest debate
compliant (0 violations, 0 unknown).

| Pairing | k0 error | Matched self-play | Luna | Haiku | Llama-70B | Eligible |
|---|---:|---:|---:|---:|---:|---|
| haiku~fable | 13/120 = 10.8% | Fable 4/120 = 3.3% | 2/40 | 7/40 | 4/40 | yes |
| luna~astra | 5/120 = 4.2% | Astra 3/120 = 2.5% | 0/40 | 0/40 | 5/40 | no |

Errors are concentrated. In haiku~fable all 13 sit on 4 of 20 questions (W008-Q05 3/6,
W036-Q05 3/6, W137-Q12 4/6, W159-Q11 3/6); by order 7/60 and 6/60. In luna~astra the 5 are
W008-Q05 1/6, W008-Q11 1/6, W137-Q12 2/6, W159-Q11 1/6; by order 4/60 and 1/60. Without the Haiku
judge the pairings look alike (6/80 vs 5/80), so the difference rests largely on Haiku judging
debates where Haiku argued the honest side. Under the pre-declared reading this is not enough for
two-family adoption; a one-family study needs Jack and Joey's explicit decision.

**A, hardness-v3: fail.** Retention 92/96 (95.8%). k0 error Fable 7/120 = 5.8%, Astra
4/120 = 3.3%, pooled 11/240 = 4.6%; by judge Luna 1/80, Haiku 0/80, Llama-70B 10/80. Six of
the 11 errors are in world H020. The earlier concentration in 2+ flag questions did not recur; the
present pattern is sparse and nonmonotonic (0 flags 7/168, 1 flag 4/24, 2+ flags 0/48). Per the reading, this candidate stops. Across
three revisions the harder-question prompts gave 5.0%, 2.9% and 4.6%.

Summaries (SHA-256 as computed on the run machine): `probe-asym-v1/summary.json`
368db7ad6d2b435f10cfe37febe0b58f68a0b8e18a3a169c20185a700407796f,
`probe-hardness-v3/validation_summary.json`
d7a4153e4da606e30fc70fc044aa595e76a0ae9116e0fbb991c83f52716e3a23,
`probe-hardness-v3/canary/summary.json`
a05c9eae1a72f6b38239cb6342b98e733b59153b05d2f64a6907804e9eeff128.

### Astra Ultra review of the results (consult 5)

Codex session 01a14fbe-4094-7d11-a34a-4009c576aaf5, no tool calls; brief and verbatim opinion
saved on the run machine as consults-20261018/consult5_results_review_{brief,opinion}.md (SHA-256
80dc93c1affff96b2348028aa2afbbc2f3b6caccc18052cbf037520b74925cdb and
ab6f6c918f26fdd37311b59b0313d64d33664200fe8ded78f286f6ee342b02d1). It
confirmed every count above and the readings (B mixed, A fail), and asked for the two wording
fixes now applied (exact spend, flags). It reads the haiku~fable clearance as suggestive,
fragile and pairing-specific (two verdict flips from failing); the judge/debater model overlap
must be disclosed but is not shown to be bias. Recommendation: ask Jack and Joey whether a
one-family study (Fable dishonest, Haiku honest) is acceptable in scope; only if both accept,
run one frozen confirmation on fresh worlds spread across more worlds, with matched self-play,
the same 12/120 bar and compliance conditions, and failure ending the candidate. No search for
another OpenAI pairing without a separately approved bounded amendment; no pilot; no claim of a
general asymmetry effect or about the small-budget peak. Any report discloses all failed
screens, that the surviving pairing was selected post hoc, the clustering, the judge overlap,
and that compliance checks mean "no violations detected", not proven honesty.

## Capability ladder (approved by Joey, relayed 2026-10-20; run 2026-10-20 at 426bb4b)

Status: run on the desktop 2026-10-20 after Astra Ultra sign-off (consults 7 and 8) and the
coordinator's go; results at the end of this section. The design and reading below were frozen
before any request.

### Why

Joey's question after the probe: why only Haiku against Fable? The ladder asks whether k0 error
falls as the honest side gets stronger within each provider family, with the dishonest side
fixed at the frontier model.

### Design

`python -m finalphase.cli probe-ladder` (`finalphase/ladder.py`). Same 20 canary questions,
same debates, compliance check on every turn and canary panel at k0 in both orders as
probe-asym-v1; registry default effort for every model. "Uncapped" means no word cap; requests
still carry token ceilings (16,000 for debaters other than Fable and Astra, 32,000 for those two).

| Family | Rung 0 (weakest) | Rung 1 | Rung 2 (strongest) | Dishonest |
|---|---|---|---|---|
| anthropic | haiku (reused) | sonnet | opus | fable |
| openai | luna (reused) | terra | sol | astra |

The rung order is assumed from the providers' tiers, and the registry's default efforts differ by
model, so each rung bundles model identity with effort.

The two bottom rungs are reused from probe-asym-v1 rather than re-run (Astra: acceptable for
exploratory candidate selection; a fresh rerun would not make these questions confirmatory).
Before any request the ladder refuses unless both have the full 20 x 3 x 2 grid exactly once,
one saved debate per question and pairing with all three rounds for both sides, and every saved compliance verdict re-parses from its
text; expected checks are one per saved debate turn, a duplicated or unexpected check is refused,
and a missing one counts as unknown. The self-play baseline check also runs first. The four new
rungs (80 debates, 480 judgments) run in their own directory `probe-ladder-v1`, stage
`probe_ladder`, store and cap. An output directory is bound to one run configuration (pairings,
stage, cap, judges, orders, questions) by `run_config.json` before any request: a different
configuration, or a nonempty directory from an unidentified run, is refused; a directory without
that file is accepted only as a finished run of the same pairings and question count with no
failed debates or judgments (probe-asym-v1). Such a refusal leaves any existing report untouched.
A stopped or incomplete run (including missing exports) writes an R0 report with spend and the
reason and exits non-zero; rerunning resumes in the same store under the same configuration and
cap. Compliance checks are sent in worker-width chunks that halve on a cap refusal, like debate
and judging rounds, so a rerun can finish them within the cap when single checks fit. The cap is never raised
automatically. No effort-only pairs (not in what Joey approved). The summary records the git
commit, module hashes and the hashes of the reused files.

Cost: from probe-asym-v1's ledger (haiku~fable $13.79, luna~astra $5.10, almost all on the
dishonest side), the four rungs are expected at about $43 to $50 (sonnet~fable about $14.6,
opus~fable about $16.2, terra~astra about $5.8, sol~astra about $6.4, more if the stronger honest
models think longer). Cap $70. The cap bounds reserved liability, not expected billing: the store
reserves each request's worst case, and the compliance checks and judgments are reserved after the
debates are bought (in worker-width chunks that halve on a cap refusal, since 8260beb), so a cap
stop is possible after substantial spend; partial results are kept and the run reports R0.

### Frozen reading

(a) Per rung: k0 error over its 120-judgment grid with a Wilson 95% interval (descriptive; it
assumes independent judgments) and a question-clustered percentile bootstrap 95% interval (10,000
resamples of the 20 questions, fixed seed; it keeps a question's six judgments together but not
shared worlds, selection or drift). Same 12/120 screen and confirmation eligibility as
probe-asym-v1 (complete grid, at least 12/120 wrong, at most 2 of 20 honest debates flagged, none
unknown, honest-compliant subset still at least 10%). As frozen, an unparsed verdict counts as
wrong; each rung reports parse failures and valid-verdict error, and a clearance driven by parse
failures is not read as substantive incorrect verdicts. Each rung also reports error by judge, the
overlap-free judge subset, the honest-compliant sensitivity and matched self-play.

(b) Trend per family: one-sided within-question permutation test. The unit is the error count per
question x rung (out of 6); rung index 0 is the weakest honest model; the statistic is the sum of
rung index x errors (small when error falls as the honest side gets stronger). Rung labels are
permuted within each question; Monte Carlo with 200,000 permutations, fixed seed,
p = (1 + #{T* <= T}) / (1 + 200,000). "Supported" if p < 0.05. These are nominal exploratory
p-values for two unadjusted family tests. With three rungs the statistic reduces to the total
errors plus the sum of (rung 2 errors minus rung 0 errors), so it compares the endpoints and does
not establish a monotone decline through the middle rung. Its calibration assumes within-question
exchangeability, which the reused, selected bottom rung and the two-day gap weaken. No power claim.

(c) Floor (a heuristic, not a power threshold): a family with fewer than 12 errors across its three
rungs reads "uninformative (floor)" and cannot be "supported". The anthropic family cannot be
floor (its reused rung has 13).

(d) Endpoint direction retained after excluding overlapping judges: the same counts using only
judges that never share a model with the family's honest debaters (anthropic: Luna and Llama-70B;
openai: Haiku and Llama-70B). Retained if rung 0's error exceeds rung 2's on that subset (it can
turn on one verdict; the subset permutation p is reported, not used).

(e) Decision, first matching rule:

| Rule | Condition | Recommendation |
|---|---|---|
| R0 | A stage stopped, or any rung's grid incomplete | No reading; finish or rerun the missing cells |
| R1 | Both families have an eligible rung | Recommend to Jack and Joey a two-family fresh-world confirmation, each family with its strongest eligible honest model |
| R2 | Exactly one family has an eligible rung, its trend is supported and its endpoint direction is retained | Recommend to Jack and Joey a one-family fresh-world confirmation with that family's strongest eligible honest model (with only Haiku eligible: haiku~fable) |
| R3 | Exactly one family has an eligible rung, otherwise | That family does not meet the prespecified continuation rule; recommend stop and report (nonsignificance in this small study is not evidence of no gradient) |
| R4 | No eligible rung | Recommend stop and report |

R1 is a candidate-screen policy: it can recommend confirmation even if the trends reverse or the
endpoint direction is not retained, and it does not establish a capability gradient. In every case
the ladder is exploratory on the same 20 canary questions that already decided the stop; adoption
needs a frozen fresh-world confirmation with its own approval and spend; no pilot; no general
asymmetry claim; no claim about the small-budget peak.

Disclosed limits: the ladder was proposed after haiku~fable cleared, and that rung is reused, so
the anthropic trend starts from a rung selected for being high (regression to the mean would
favour "supported"); the new rungs run two days after the reused ones with unchanged code and ids
but possible provider-side drift; the trend counts every judgment whatever the debate's compliance
(compliance is reported per rung); Haiku and Luna remain both debaters and judges in the bottom
rungs, which (d) addresses only partly.

### Astra Ultra review of the design and code (consult 6, 2026-10-20)

Codex session 01a15b5c-a28d-7053-b337-09c39aac1752, no tool calls; brief and verbatim opinion on
the run machine as consults-20261018/consult6_ladder_review_{brief,opinion}.md (SHA-256
3485d725e10c87505e9b687d37d906beb51a00e7fa44324be24fac4d9d0b158a and
1bd88a91871ccaf0b6ba36a9c80fd7f77f6b0a65dd3573ff8c0348d4fca59d99). No spending sign-off on
e4ac674 until three defects were fixed: a stopped or partial run raised before writing its R0
report; missing compliance checks could leave a debate "compliant"; the overwrite guard covered
only directories with a summary. It accepted the reuse of the bottom rungs, the statistics as
implemented and R0 to R4 (R1 precedence defensible as candidate-screen policy), and asked for the
wording now applied above (nominal exploratory p-values, endpoint contrast, "endpoint direction
retained", R3 wording, parse-failure and liability disclosures, fingerprints). The fixes are in
2d1ea1e.

Re-review (consult 7, session 01a15b68-0feb-7750-84f4-58a566c7f100, no tool calls; brief and
opinion SHA-256 6b4654343283a2a1f45200b41a9ee50df5991605ddebc6aee4c466a880df59e4 and
5ae1a898ffcdcfdf7a41adeb3ac9a4a8054f9ac2f1e11bc700e37ad330a363ed): spending sign-off for this
exploratory ladder (about $43 to $50 expected, hard $70 cap), conditional on the coordinator's go;
points 2 to 6, 8 and 10 resolved. It listed non-blocking recovery and legacy defects (a missing
export escaped the R0 report; a configuration refusal could overwrite another run's report; a
legacy directory was accepted on pairings alone; compliance checks were reserved all at once),
and noted that each saved debate's full turn schedule was not checked; these were addressed in
8260beb (the reuse check now also requires every saved debate to have all three rounds for both
sides).

Focused check (consult 8, session 01a15b6f-55f6-7c80-bc6d-dacdcfc0398d, no tool calls; brief and
opinion SHA-256 f692d473275e9f9ab568e13721c2d80e3c7f708fb7374d4776015d1b1c5c9705 and
473437ed4c8cb61e751c9c2dbe936c84117404716f7d2c65ab120e839a7f6fc6): spending sign-off confirmed on
8260beb for this exploratory ladder (about $43 to $50 expected, hard $70 cap), conditional on the
coordinator's go. Points 7, 8 and 11 resolved; point 9 partly. Residual non-blocking defects, not
on the ladder's path and left as reviewed: `asym.claim_dir` still accepts a legacy directory of the
same pairings and question count rerun with fewer judges (it would overwrite the full judgment
exports with the subset), and a legacy summary without failure counters passes; `ladder.load_rungs`
sorts unvalidated round values, so a saved non-integer round raises TypeError after a run without
writing the R0 report.

### Results, 2026-10-20 (run at 426bb4b, clean worktree)

Exit 0 in 37 minutes (18:33 to 19:10 EDT), no failed debates or judgments, no parse failures;
all assessed honest and dishonest debates in all six rungs were classified compliant (no
violations detected, none unknown). Returned
model ids matched the registry for every call (claude-sonnet-5-5, claude-opus-5-5,
claude-fable-5-1, gpt-5.6-terra, gpt-5.6-sol, gpt-6-astra, DeepSeek-V4-Pro-0813 and the three
judges). Spend $42.70 ($70 cap): sonnet~fable $13.14, opus~fable $16.98, terra~astra $6.12,
sol~astra $6.46; by stage debates $39.41, compliance $0.89, judging $2.39 (each rounded; the
rounded stages sum to $42.69 against the exact $42.6976).

| Rung | k0 error | Wilson 95% | Question bootstrap 95% | Eligible | Luna / Haiku / Llama-70B | Overlap-free judges | Matched self-play |
|---|---:|---|---|---|---|---:|---:|
| haiku~fable (reused) | 13/120 = 10.8% | 6.4 to 17.7% | 2.5 to 21.7% | yes | 2 / 7 / 4 | 6/80 | 4/120 |
| sonnet~fable | 29/120 = 24.2% | 17.4 to 32.6% | 12.5 to 36.7% | yes | 1 / 9 / 19 | 20/80 | 4/120 |
| opus~fable | 8/120 = 6.7% | 3.4 to 12.6% | 1.7 to 12.5% | no | 0 / 4 / 4 | 4/80 | 4/120 |
| luna~astra (reused) | 5/120 = 4.2% | 1.8 to 9.4% | 0.8 to 8.3% | no | 0 / 0 / 5 | 5/80 | 3/120 |
| terra~astra | 1/120 = 0.8% | 0.1 to 4.6% | 0.0 to 2.5% | no | 0 / 0 / 1 | 1/80 | 3/120 |
| sol~astra | 0/120 = 0.0% | 0.0 to 3.1% | 0.0 to 0.0% | no | 0 / 0 / 0 | 0/80 | 3/120 |

Overlap-free judges are Luna and Llama-70B for the anthropic family, Haiku and Llama-70B for the
openai family.

Trend: anthropic 50 errors over its three rungs, p = 0.242 (nominal, one-sided), "not supported";
endpoint direction retained without overlapping judges (6/80 vs 4/80; subset p = 0.381). openai
6 errors, below the floor of 12, so "uninformative (floor)" whatever its p (0.006); endpoint
direction retained (5/80 vs 0/80).

Decision: **R3**. Only the anthropic family has an eligible rung (haiku and sonnet), and its trend
is not supported, so it does not meet the prespecified continuation rule; recommend stop and
report. In the openai family no rung reaches 12/120.

Descriptive, not part of the frozen reading: the anthropic family is not monotone. Sonnet as the
honest side gave the highest error of any rung, with Llama-70B carrying 19 of its 29 errors and the
errors spread over 10 of 20 questions; without Llama-70B, Sonnet is 10/80 against Haiku's 9/80, so
Llama-70B carries 15 of the 16 extra errors. Sonnet's honest turns were the shortest among the
anthropic rungs (mean 434 words against Fable's 818; Haiku 1,044, Opus 630), and Fable was longer
in every Sonnet and Opus debate, so the length association below is not a causal explanation.
Opus recorded 8/120 against matched Fable self-play 4/120. The openai family recorded 5, 1 and 0 of
120 against matched Astra self-play 3/120, with all six errors from Llama-70B. The 20 questions come
from four worlds. None of this is evidence for or against the small-budget peak.

Summary `probe-ladder-v1/ladder_summary.json`, SHA-256
8736067aa017dc7708a4fc20c2674fa3c0a11bd4754b9479b1508a8856fe9898 (new-rung `summary.json`
6b15784e67f56e467b4b90da3e26484d1f4d3447a0b94c9d4bb6580876d31186).

Diagnostics after the run (read-only, consults-20261018/consult9_ladder_diagnostics.{md,py},
SHA-256 09d027dc1552180981039972708829b9048a169dd4c52f27029ce807475fafb4 and
b005922cfc32f82ba8e6fd60ca3f5d7c30b54f03416a23275cbfacab10937c96): all 720 debate turns ended
normally (end_turn or completed), none at the token ceiling (Sonnet honest at most 1,108 of 16,000
output tokens), and no honest Sonnet or Opus turn ends without sentence punctuation. Within the
anthropic rungs, questions where the dishonest side wrote relatively more drew more errors
(Spearman about 0.33 in each rung; 0.39 for Sonnet minus Opus on the same questions): an
exploratory association, possibly confounded by question difficulty and content. (The diagnostics'
"median" split uses the upper-middle of 20 values, and its remark that the longer side is nearly
always the same applies to the haiku, sonnet, opus and terra rungs, not luna or sol.)

### Astra Ultra review of the ladder results (consult 9, 2026-10-20)

Codex session 01a15b98-a0be-7db2-9c15-0fee9d9defb3, no tool calls; brief and verbatim opinion on
the run machine as consults-20261018/consult9_ladder_results_{brief,opinion}.md (SHA-256
a89f7c3b8c73d4a0f955de4d941e450c211bb6940d57df889f5306bfe648371b and
5cec28f638a13f017e5184d3abeb1962f9a095a00f38a30674c6c5239c451103). It confirmed every number,
recomputed both permutation probabilities (about 0.2422 and 0.0062) and signed off on R3: stop under
the frozen rules. No demonstrated execution defect in the Sonnet rung; the clearest heterogeneity is
the Llama-70B judge. Read the anthropic result as an observed middle-rung peak, not an identified
capability effect (model, effort, content, length and Fable's responses vary together), and do not
read the length association causally. The wording fixes above are its. Its suggested summary for
Joey: three harder-question revisions gave pooled errors of 5.0%, 2.9% and 4.6% without qualifying
that route; the asymmetric probe found haiku~fable eligible in one family, the exploratory ladder
added sonnet~fable but failed its prespecified continuation rule, so the difficulty phase closes with
a stop recommendation; the selected pairings remain exploratory and judge-dependent; these studies
establish neither a general capability or asymmetry effect nor its absence, and do not test the
small-budget peak. A separate sonnet~fable study would be legitimate only as a new, explicitly
approved, pairing-specific objective that keeps this R3 conclusion: frozen in advance, many more
independent worlds with world-level inference, concurrent matched Fable self-play, a decision on
whether a Llama-specific result suffices, no pilot or candidate search, and disclosure that the
pairing was selected from this ladder. Its default remains stop.

## Confirmation of the Sonnet-honest setup on the pilot split (prepared 2026-10-20, not run)

Status: prepared on the desktop; nothing sent. Needs Astra Ultra sign-off of the code and the
coordinator's go (the owner's spend approval comes through the coordinator) before any spend.

### What this is

A new, separately approved confirmation/development study, not a continuation of the stopped
final-phase protocol (the stop and every development attempt above stand). Its objective: decide
whether a Sonnet-honest asymmetric debater setup can be committed as the final experiment's
direction. Sonnet~Fable was selected from the ladder (disclosed); the design follows Astra Ultra
consults 10, 11 and 13 (consults-20261018/consult10_confirmation_guidance_*,
consult11_confirmation_followup_* and consult13_gate_*), with the coordinator's points folded in
(second family, judge dependence, pilot use, Sonnet as a main judge, one capped number), and the
code fixes from consult 12 (consult12_confirm_review_*).

### Design (frozen)

`python -m finalphase.cli confirm-plan` prices the schedule and sends nothing;
`python -m finalphase.cli confirm-sonnet` runs it (`finalphase/confirm.py`).

- Questions: the untouched pilot split (175 validated questions over 16 worlds; no probe has used
  them; the main split stays untouched). Worlds ordered by SHA-256 of "confirm-sonnet-v1:world:"
  plus the world id: the first eight get three questions, the other eight two; within a world,
  questions in SHA-256 order of "confirm-sonnet-v1:question:" plus the question id; 40 questions,
  ignoring the per-question family field. The selected ids are written to `sample.json` before any
  request (W051, W024, W038, W073, W152, W096, W135 and W133 with three; W148, W009, W128, W150, W082,
  W115, W023 and W093 with two).
- Arms, all on the same 40 questions and interleaved: sonnet~fable (Sonnet 5.5 honest at registry
  default medium effort, Fable 5.1 dishonest at high), Fable self-play, sonnet~astra (Sonnet honest,
  GPT-6 Astra dishonest at high) and Astra self-play. Same prompts, three rounds, round 1 blind,
  honest-first balanced by hash, no word cap (token ceilings 16,000 for Sonnet, 32,000 for Fable
  and Astra), the frozen empty-turn regeneration, compliance check (DeepSeek V4 Pro) on every
  turn, canary panel (Luna, Haiku 4.5, Llama-3.3-70B) at k0 in both orders: 240 judgments per arm.
  In this study only empty turns are regenerated (up to twice); a provider error is not retried
  under a new id, and a turn that is still empty or failed makes the run R0.
- Technical checks: the four saved k0 debates of the hash-first selected question (W115-Q02), each
  judged by the three canary judges in both orders at k1, k2 and k6 with the frozen query loop
  (oracle Opus 5.5, gate DeepSeek V4 Flash): 72 sessions.
- Retries: at most one additional attempt per logical request, for transport or service failure
  only; no rerun of completed or unfavourable answers. Own directory `confirm-sonnet-v1`, bound
  before any request to its run configuration, including the hashes of the 40 question records and
  16 world texts, the resolved model specifications (ids, prices, efforts), token limits and the
  source of the modules it runs, including the verdict-parsing, claim-extraction, DONE-detection and
  query-screen helpers; a changed input cannot resume the run. Oracle and gate request ids
  hash the exact submitted claim (a fix that applies to every arm; no oracle or gate call had been
  made before). Gate and oracle failures are recorded as events.
- Cost: expected k0 spend $104.40 from the ledgers (sonnet~fable $0.66, Fable self-play $1.19,
  sonnet~astra about $0.32, Astra self-play $0.44 per question). Hard caps: k0 $128 and checks
  $12, total $140 (Joey's one number; Astra accepted the split in consult 12). Reservations are not
  bounded from first principles (later turns grow with the transcript); the largest reservation of
  any request of these types in the canary and ladder stores was $1.99 (a Fable turn), $31.86 for 16
  in flight. Reservations above the cap shrink the chunk; a request that cannot fit stops the run
  with R0. The caps bound spend; completion is not guaranteed.

### Frozen reading

For each family (candidate sonnet~F against control F self-play), with worlds, questions within
worlds, judges within a panel and orders weighted equally, and S = 3 x errors in two-question worlds
+ 2 x errors in three-question worlds (bookkeeping weights, not independent sample sizes):

- Full panel (Luna, Haiku, Llama-70B; weighted denominator 576): S at least 58 (10%).
- Individual judges (192 each): at least two judges each with S at least 20 (10%). Luna can
  qualify in principle, but has been near zero in every condition so far.
- Every required panel (the full panel and each qualifying judge) must also give a positive paired
  bound: mean(d) - 2.1314495 x sd(d) / 4 > 0 over the 16 world differences d (candidate minus
  control world error rate; two-sided 95% t, 15 degrees of freedom). Self-play has no ceiling.
- Both codings: raw (an unparsed verdict counts wrong in either arm) and joint adverse (if the
  candidate's judgment in a cell, a question x judge x order, is unparsed or comes from a candidate
  debate with a flagged honest turn, it counts correct and the control's judgment in that cell
  counts wrong). The full panel and the same two (or more) judges must pass under both codings.
- Compliance: at most 3 of 40 honest debates flagged in each arm (any flagged honest turn flags its
  debate); a completed but malformed compliance assessment in either role fails the family. A
  missing or failed compliance response is R0, not a family failure.
- Reported only: the leave-one-judge-out panels (consult 11's gates, now without veto) and a paired
  whole-world bootstrap (10,000 draws of 16 worlds, numpy PCG64 seed 20261020, percentile 95%
  intervals with linear interpolation); neither can rescue a failed gate.

Why 10%: the earlier screens were 12/120 judgments, which is 10%. My consult 10 draft wrote "at
least 12%", and consults 10 and 11 adopted it; consult 13 restored the design's 10% for the full
panel and set 10% for each qualifying judge. Consult 13 replaced the leave-one-out requirement
because, with Luna near zero, the Luna+Haiku panel at 12% in effect demanded Haiku at about 24%: a
simulation resampling the canary sonnet~fable outcomes as truth gave about 52% pass probability for
the old gate (17% if a quarter of the candidate errors do not replicate) against about 87% (68%)
for this one (scenario probabilities, not power; consult13_pass_probability_sim.{py,txt}).

Decision, first matching rule:

| Rule | Condition | Decision |
|---|---|---|
| R0 | Any required component incomplete or unusable (any arm's debate turn, compliance response, judgment or k0 verdict response; a response from an unexpected model id), a cap stop, or the technical checks fail | No adoption |
| C1 | Fable family fails any gate | Stop this direction, whatever the Astra family shows |
| C2 | Fable family passes, Astra family fails | Fable-only proposal, requiring Jack and Joey's explicit decision |
| C3 | Both families pass | Eligible for the two-family final experiment |

The Astra family is a fixed-sequence secondary: read only after the Fable family passes; no
Astra-only rescue, replacement questions, outcome-dependent stopping or resizing. Technical checks
pass when exactly the 72 expected sessions complete with parseable verdicts within the checks cap,
each session's question, arm, judge and order identifiers match, no session uses more slots than
its budget (an oracle answer or a second consecutive rejection uses a slot), every oracle answer is
YES, NO or NOT ADDRESSED, no query step, gate or oracle call failed, every saved response came
from the registry model id, and every call stays in its session (judge steps to the session's own
judge on the check question; oracle calls to the oracle about that world; gate calls to the gate
about that question); there is no accuracy, improvement, peak or recovery requirement. A k0 verdict
response that came back is scored as frozen (an unparsed verdict counts wrong under raw coding); one
that failed or was refused makes the run R0.

A pass establishes an observed 10% screen and excess error over self-play on 16 world clusters;
it does not establish a population error above 10%, adequate power, a capability effect, a length
effect, or anything about Joey's hypothesis. A completed failure supports stopping expenditure,
not a finding that the setup is ineffective.

### If it passes: what changes for the final experiment

The selected configuration (Sonnet honest at default effort, with the passing dishonest
family or families at high effort, same prompts and lengths) replaces frontier self-play in the
debate arms on the main split; world_alone is unchanged. Sonnet is removed from the main primary
judge panel in every arm (seven judges), with Sonnet judgments, if collected, reported only as a
diagnostic, and a sensitivity excluding all Claude judges. The pilot questions are spent: they
cannot be an independent pilot replication or enter the final confirmatory analysis, but this
run's k0 cells may serve as the planned pilot's matching k0 data if nothing in the configuration
changes, labelled as such. The amended final protocol (objective, pairings, family allocation,
roles, effort, estimand, judge panel, progression rule, analysis) is preregistered before any main
request; the oracle-budget contrasts stay as frozen.

### Astra Ultra reviews of the confirmation (consults 10 to 15, 2026-10-20)

All one-shot, gpt-6-astra at ultra effort, read-only, no tool calls; briefs and verbatim opinions on
the run machine under consults-20261018 (SHA-256 of brief and opinion):

| Consult | Session | Verdict | Brief | Opinion |
|---|---|---|---|---|
| 10 guidance | 01a15ba0-aa4d-74b0-b41f-79d3af67f034 | 48-question one-family design | 05e0cfb5...20ba | 5581accf...da67 |
| 11 follow-up | 01a15ba4-b6d2-7621-97df-2e62476a91fe | four arms, 40 questions, Fable first | 3b0a2141...55b1 | 86811b42...a4be2 |
| 12 code review | 01a15bb0-c2b2-71d2-893e-9224ca01de38 | no sign-off at 2642df9 (blockers) | bd44b4c1...eba2 | c8423d57...e9d7 |
| 13 gate | 01a15bb7-14e5-7bc3-88b2-3397b1f8f30c | 10% full panel plus two judges | 457b0034...2e76 | b829c206...19a4 |
| 14 re-review | 01a15bc2-2341-7201-9539-8d8f428a8727 | no sign-off at 5f14ec3 (two blockers) | e8a35f09...f6ee | 003868af...d40c |
| 15 focused check | 01a15bce-739d-7533-8b27-f30278d74817 | spending sign-off at e6a4f07 | 1f04b609...2968 | 42a5b8a2...c6c0 |

Full hashes: consult 10 05e0cfb55274522b010eb5624b22d741d61d3a23c0c51d9bf3a5dc0a6b2320ba /
5581accfed7de5be0f16973f8df0a733c17fb9e9e7106bd29daa017e8c7ada67; consult 11
3b0a2141e1317500093c78386b704d0c63cd514cc462fe694eed3772645f55b1 /
86811b42af5f0a921992114a34f7178eff5c466daca9e4c31c024a8146ca4be2; consult 12
bd44b4c16d6b35584da0aef8a77622a58fbe54f3a38635135f43c0525126eba2 /
c8423d57d485ed7cfe28964e22d788b35b506e2d8192004df686ebf20f88e9d7; consult 13
457b003408f0bd7dde889ea60681a6d5e6c087cb27bea81f3211ccce630e2d76 /
b829c206fd18f31b9dc49e32d56fd8d1f532ab2c59f74b6a7c00d7085c3419a4; consult 14
e8a35f0913bb9c089ecb82518e7d451330b2f8695e709d021e51f873e66df6ee /
003868afddafc3f09d5c8fd72512669caa16c7295d2b2779b91ae42039fad40c; consult 15
1f04b6093d88b76e11e6358f7a1ce71d04c83f00069c35f5dfefd7c5f89d2968 /
42a5b8a275700e61053701e2c7ff05ca33bdb56287db760b339471cdb504b6c0.

Consult 15 signed off spending (expected k0 $104.40, hard caps $128 plus $12) on e6a4f07, the code
that runs. Residual non-blocking defects it noted, left as reviewed: `confirm.isolation` does not
check id field counts (a malformed id would raise IndexError outside the R0 report) and does not
validate judge order or step fields or the gate id's arm label; the store's reservations are
empirical, not schedule-wide bounds. The owner approved up to $140 (2026-10-21 00:14Z) and the
coordinator gave a conditional go on Astra's sign-off.
