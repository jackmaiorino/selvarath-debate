# Why the Judge Gets Worse: Mechanism Audit and Packaging Test

**Date:** 2026-07-12 · **Cost:** $14.57 · **Data:** Stage-1 records plus 3,816 fresh-context replays
**Reliability:** mechanism labels from two independent blind passes, agreement 18/23 (kappa 0.68)

Two follow-ups the leads asked for: (1) what actually causes the judge to flip from right to wrong
when it gets oracle access, and (2) how much of the harm is the interactive format itself
(the fresh-context test).

## 1. What causes the flips (balanced audit, both directions plus non-flip controls)

Cases where the judge was right with no oracle but wrong with 2 oracle calls, counted only when
consistent across repeat runs:

**Normal condition, 17 flips, two causes in roughly equal measure:**
- **The oracle is wrong (6 to 9 of 17).** Not a software bug this time: the oracle model gives a
  false "NO" to claims that are true but need a small step of reasoning ("quorum is 6, majority
  rule, so 4 delegates can decide"), and once fabricated a "YES" for an unsupported claim. The
  judge then discards a correct argument as "refuted".
- **The judge over-reads correct answers (8 to 11 of 17).** The oracle truthfully confirms a
  background fact, and the judge treats that as settling the actual disputed question, or reads
  "the text does not address this" as a point against one side.
- The pilot-era third cause, malformed queries, is essentially gone: the fixed query format
  produced well-formed, relevant questions in over 99.9% of cases.

**Fake-oracle condition, 6 flips, one cause:** with zero information coming back, the judge
re-decides based on style: which side sounds more "plain language", more detailed, less
speculative. Both labeling passes agreed on all 6.

**Controls behave:** in matched cases that did not flip, the oracle was right and the questions
relevant every time. Bad signals really do separate flips from non-flips.

The 5 label disagreements all sit on the same boundary (oracle-slightly-wrong vs judge-over-read)
and are queued for a human look.

## 2. The packaging test (fresh-context replay)

We took every judgment where the judge used the oracle and re-ran it with the judge's own
questions and answers handed over as a plain list in a brand-new conversation. Same information,
different presentation. 3,816 replays, no new oracle calls, order-shuffled variant as a check.

| Condition | wrong at 1 call | at 2 calls | at 5 calls |
|---|---|---|---|
| No oracle | 1.26% | | |
| Interactive (normal) | 3.25% | 6.09% | 5.66% |
| Same Q&A as a plain list | 2.31% | 3.14% | 2.41% |

Findings, in the wording our review allows:

- Presenting the same Q&A as a neutral list cut the error rate by 1.94 points across budgets 1-2
  (95% range 0.89 to 3.10) and by 2.94 points at budget 2 (1.26 to 4.94). **The interactive
  conversation format is a major contributor to the harm**, plausibly through the judge seeing its
  own committed questions and turn structure.
- Packaging explains 57% of the total harm as a point estimate, but the uncertainty range
  (31% to 112%) means we can say "a large share", not "most".
- With clean packaging, the leftover harm vs no oracle is small and statistically inconclusive
  (+1.47 points, range -0.16 to +3.46). Real oracle mistakes do cause specific flips (section 1),
  but on average the information itself is much less damaging than the format.
- Shuffling the list order made no difference (+0.21, range -0.42 to +0.84).
- Note we did NOT establish that more budget helps in either format: both improve slightly from
  2 to 5 calls, by about the same amount, and judges only use ~2.7 questions on average anyway.
- Caveat: the replayed Q&A were chosen by the interactive judge, so this does not tell us how a
  judge would do choosing its own questions in list form.

## 3. What this changes

The phase-2 grid now carries the plain-list condition as a core condition in every cell (it costs
one extra call per judgment), with an empty-list control at the anchor cell. The central question
sharpens: does the packaging harm shrink as judges get more capable, or is it a stable property of
how these models judge? Spec updated: `docs/superpowers/specs/2026-07-10-phase2-capability-pilot-design-v2.md`.

## Reproducibility

`uv run python -m rejudge.batch_replay` (replay runner), `rejudge/analyze_batch.py` (contrasts),
`rejudge/output/mechanism_cases_v2/` (54 case files), `rejudge/output/mechanism_passA.json`
(open-coding labels). Consults #10b-#12 in the project log.
