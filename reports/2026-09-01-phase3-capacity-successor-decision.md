# Phase 3 capacity successor decision

Date: 2026-09-01. Status: owner decision required. This document authorizes no reviewer
dispatch, provider call, main run, or spend.

## What happened

The signed attempt at source commit `558da84d2ac737c99677a83ba12498aee4a86837`
stopped before a reviewer invocation because the capacity executor passed `concurrency` to a
runner whose production keyword is `batch_concurrency`. The durable history records
`dispatch_started` followed by `attempt_completed_fail`. Its failure receipt counts 60
reservations, zero validated invocation receipts, and zero wave outputs. The failed cohort and
its retry cohort remain terminal and will not be reused.

Commit `041644018861b2a9cb4ab62c0003edde336e3fd3` repairs the keyword contract. All 100
capacity-builder and execution tests pass.

## Recommended successor

Use a semantic-preserving candidate-line-order transformation. Keep the frozen prefix, query,
candidate labels, and candidate text unchanged, but render the explicitly labeled Candidate B
line before the Candidate A line. This is not whitespace or nonce padding.

All 576 resulting prompts are distinct from the 576 sealed source prompts and all 368 v1
candidate-swap variants. Deterministic ranking yields two fresh 180-packet cohorts in three
60-packet waves, with 216 variants unused. The exact hashes and predecessor evidence are bound
in `rejudge/phase3_main_review_capacity_successor_proposal_2026-09-01.json`.

## Exact non-execution ratification

> I ratify the exact Phase 3 capacity successor proposal dated 2026-09-01, including the
> terminal predecessor failure, its 60 counted reservations, the candidate-line-order
> transformation, exclusion of all sealed source prompts and all v1 candidate-swap prompts,
> two fresh 180-packet cohorts, and 216 unused variants. This ratification authorizes offline
> plan and workload materialization only. It grants no reviewer dispatch, provider call, main
> run, or spend authority.

After ratification, the offline plan and workload can be materialized at a clean source commit.
Reviewer dispatch will still require a new exact manifest and separate detached owner signature.
