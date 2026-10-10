# Canary preparation, 2026-10-09

## Split (offline, free)

`split --main-questions 1068` ran on Jack's PC at code 66e28f7 with no provider
client. Canary: W008, W036, W137 and W159, 41 retained questions. Pilot: 16 worlds,
175 questions. Main: 140 worlds, 1,068 counted questions plus the two
sensitivity-only questions (W005-Q11 and W123-Q04), both in main and outside the
cap. File hashes: `canary.jsonl` 5475015c…708e, `pilot.jsonl` 4440d208…7f19f,
`main.jsonl` 51ca660b…47eb, `split.json` 1282d208…e036.

Open design check for main: the keyed answer is the longer one in 478 of 1,068
counted main questions (44.76% by words), just under design section 5's 45% floor.
`sample_main` does not enforce that balance. Main's size is reset from the measured
pilot before any main request, so a deterministic balance rule is added then.

## Expected canary cost

`canary --n 20` runs 20 questions with both debaters, uncapped and at 150 words:
80 three-round debates (480 turns plus cap and empty-turn retries) at list price,
then DeepSeek compliance checks on every turn and k0 judgments by Luna, Haiku and
Llama in both orders (480). World texts average 1,323 words. Expected cost is about
$60 to $135, almost all debater output at high reasoning effort; the earlier forecast
was $58.04 to $130.88. The $60 canary cap would likely halt it, so the caps are
rebalanced by Jack's decision (card, 2026-10-10 12:10Z): validate is trimmed to
$178 (closed at about $177), canary rises to $150 and pilot to $1,275. Author ($173)
and main ($3,634) are unchanged, so caps plus the $590 reserve still total $6,000.
A fresh validation plan now refuses at the trimmed cap, which is intended for a
closed stage.

## Throughput qualification

Preflight refuses the canary without a compatible serial/parallel receipt, and only
validation could produce one. `qualify-canary` now times the uncapped opening turns
of the first canary question for both debaters: a serial control arm with fresh IDs
(never used in the canary) and the canonical requests at the canary's 16 workers,
whose responses replay into the canary. It writes `canary_qualification.json` and
`throughput.canary` in the manifest. It is paid (eight debater turns, a few dollars)
inside the canary cap and needs Jack's go.

## First launch refused by worst-case reservation, 2026-10-10

`qualify-canary` passed at dcbe0fc: serial 72.3 s, 16 workers 23.4 s, four valid
opening turns, selected 16 workers, $0.56. `canary --n 20` then stopped before any
debate call with `CapExceeded: spent 0.56 + max estimate of 156 calls exceeds cap
150.00`. The store reserves every request's worst case (32,000 output tokens at list
price) before sending a round, so round 1's 160 opening turns alone reserved about
$273, although expected real spend is $60 to $135.

Fix: in live mode the driver now reserves and sends each round one worker-width
chunk at a time, halving a refused chunk; a single request that still does not fit
is a real cap stop. The cap check also stops reserving rows that were registered but
never sent (`pending` with zero attempts), such as the refused round's 156 rows;
attempted or in-flight rows stay reserved. Batch mode is unchanged. The canary's
16 workers stay busy except at chunk boundaries.

## Canary result, 2026-10-10 (stop rule triggered)

The chunked relaunch at 8e6a363 (throughput receipt carried forward by Jack's
decision) ran 13:17Z to 13:50Z, exit 0. All 80 debates and 480 k0 judgments
completed; all 1,537 calls returned ok, with no parse failures. Spend $57.96 of the
$150 cap, including the $0.56 qualification: Anthropic $42.33 (Fable $41.23, Haiku
$1.10), OpenAI $14.25 (Astra $14.11, Luna $0.14), Together $1.38 (DeepSeek
compliance $0.79, Llama $0.59).

| Screen (design section 7, uncapped) | Fable | Astra |
|---|---:|---:|
| Classifier-flagged debates (limit 2 of 20) | 0 | 0 |
| Mean words, honest / dishonest | 714 / 873 | 371 / 345 |
| k0 judge error (floor 10%) | 3.3% | 2.5% |

Pooled uncapped k0 error is 2.9% (7 of 240); by judge Luna 0%, Haiku 1.2%, Llama
7.5%. At 150 words, mean turn lengths were 141 to 147 (one Fable turn at 160) and
pooled error 2.1%. The manual reading of flagged and 20 unflagged turns is not done.

Uncapped difficulty is under 10% for both families, so the design's rule applies:
stop and report to Jack and Joey before the pilot. No pilot request is made.
