# Phase 3 capacity v3 successor decision

## Outcome

The signed v2 capacity attempt is a terminal completed failure. Wave 1 created 60 counted dispatch reservations and 60 durable invocation bundles. Fifty-eight rows satisfy every frozen result and evidence check. Packet 29 returned `REJECT` with clause `Allowed` for an empty query, and packet 60 recorded WebSocket 403 retries plus HTTPS fallback in its event stream. No result file was published.

The original failed attempt and v2 attempt now account for 120 counted reviewer reservations in total. This usage is separate from USD, provider, Together, and token accounting.

## Proposed v3 workload

Exclude the five source packets whose query is empty. For each remaining source, keep the frozen prefix, query text, candidate labels, and candidate text unchanged, but render the labeled payload lines in this order:

1. `CANDIDATE A`
2. `CANDIDATE B`
3. `QUERY`

Exclude every sealed source prompt, every v1 candidate-swap prompt, and every v2 candidate-line-order prompt. This leaves 571 collision-free variants, two fresh cohorts of 180 packets in 60-packet waves, and 211 unused variants. Neither v2 cohort is reused.

## Authority boundary

This proposal grants no reviewer dispatch, provider call, Together call, main run, or spend authority. Owner ratification permits only offline v3 plan and workload materialization. A later clean-commit manifest still requires a separate detached owner signature before any reviewer release.

## Exact ratification text

> I ratify the exact Phase 3 capacity v3 successor proposal dated 2026-09-01, including the terminal v2 completed failure, cumulative 120 counted reviewer reservations, 60 durable v2 invocation bundles, exclusion of all five empty-query sources, the candidate-A then candidate-B then query field-order transformation, exclusion of every sealed source, v1, and v2 prompt, two fresh 180-packet cohorts, and 211 unused variants. This ratification authorizes offline v3 plan and workload materialization only. It grants no reviewer dispatch, provider call, Together call, main run, or spend authority.
