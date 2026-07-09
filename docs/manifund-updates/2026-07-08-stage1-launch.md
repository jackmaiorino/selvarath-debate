# Manifund update: Stage 1 validation run launched

Posted: (pending) · Project: https://manifund.org/projects/testing-failure-modes-of-debate-style-ai-control-schemes-tewkbpvy1s
Each section below maps to one field of the Manifund "post update" popup. Paste as-is.

## What progress have you made since your last update?

We re-analyzed the pilot data and quantified the headline effect: a small oracle budget raised the dishonest debater's win rate by +7.2pp (95% CI [4.6, 10.2]).

Before scaling up, we audited the pilot code and found two serious bugs in the oracle channel: every "NOT ADDRESSED" reply was miscoded to "NO", and ~100% of oracle queries were sent garbled. We retracted the mechanism conclusions and corrected the write-ups.

We then rebuilt the harness and launched a pre-registered validation run (in flight now, ~$150-200 of the grant): the same 318 transcripts re-judged under six arms, including a clean harness, a faithful bug replay, and a placebo oracle. Gates were frozen before any clean data existed. Pre-registration: https://github.com/jackmaiorino/selvarath-debate/blob/rerun-new-models/docs/rejudge-protocol.md and corrected report: https://github.com/jackmaiorino/selvarath-debate/blob/rerun-new-models/reports/2026-07-06-preliminary-findings.md

## What are your next steps?

The pre-registered gates decide: if the effect survives the clean harness, we proceed to the proposed judge x debater capability grid (most of the grant). If the placebo explains it, we pivot to studying deliberation effects. If it collapses, we publish the artifact result and a decomposition of what each bug contributed. Write-up either way, including negative results.

## Is there anything others could help you with?

Methods scrutiny of the pre-registered protocol before results land, and pointers to related work on oracle/verification interfaces or deliberation-length effects in LLM judging.
