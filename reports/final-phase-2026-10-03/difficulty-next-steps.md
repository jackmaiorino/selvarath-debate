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
