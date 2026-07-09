# Manifund update: Stage 1 validation run launched

Posted: (pending) · Project: https://manifund.org/projects/testing-failure-modes-of-debate-style-ai-control-schemes-tewkbpvy1s
Each section below maps to one field of the Manifund "post update" popup. Paste as-is.

## What progress have you made since your last update?

We re-analyzed the pilot data and quantified the headline effect: a small oracle budget raised the dishonest debater's win rate by +7.2pp (95% CI [4.6, 10.2]).

Before scaling up, we audited the pilot code and found two serious bugs in the oracle channel: every "NOT ADDRESSED" reply was miscoded to "NO", and ~100% of oracle queries were sent garbled. We retracted the mechanism conclusions and corrected the write-ups.

We then rebuilt the harness and ran a pre-registered validation experiment ($182 of the grant): the same 318 transcripts re-judged under six arms, including a clean harness, a faithful bug replay, and a placebo oracle, with gates frozen before any clean data existed. Results: the harm signal survives the clean harness but at half the pilot's size (+3.4pp, 95% CI [+1.3, +5.9], vs the pilot's +7.2pp). The two bugs contributed about half the original effect, almost all of it from the NOT-ADDRESSED miscoding. A placebo oracle shows extra deliberation turns alone cause a small degradation (+1.6pp), and the clean harness does not reproduce the pilot's U-shaped recovery at higher budgets. Pre-registration and amendment: https://github.com/jackmaiorino/selvarath-debate/blob/main/docs/rejudge-protocol.md and corrected report: https://github.com/jackmaiorino/selvarath-debate/blob/main/reports/2026-07-06-preliminary-findings.md

## What are your next steps?

The original large-effect gate came out indeterminate (real but below the 4pp bar), so per our pre-committed amendment we proceed to a reduced-scope judge x debater capability pilot rather than the full grid, plus a full Stage-1 write-up. The open question the pilot targets: does the remaining harm shrink as judge capability grows, and is it verification content or deliberation burden?

## Is there anything others could help you with?

Methods scrutiny of the pre-registered protocol before results land, and pointers to related work on oracle/verification interfaces or deliberation-length effects in LLM judging.
