# Phase-3 main-protocol pre-review

Date: 2026-08-29. Status: offline continuation review, not final item-6 ratification. This document authorizes no provider call or spend.

## Decision

Do not freeze a main-run execution identity or request the proposed $875 authorization yet. The canary supports a conditional funding ask for the two named judges. This continuation fixed the journal's unsafe timeout and open-reservation assumptions. Owner confirmation of the scientific scope and a valid review-capacity preflight remain unresolved, and the main-only runner and analysis pins do not yet exist.

## Proposed science preserved pending owner confirmation

- Named endpoints: Qwen3.8-2.4T-A95B and Llama-3.3-70B-Instruct-Turbo. Claims stay endpoint-specific and do not generalize to model scale or a broader judge population.
- Inventory: 82 questions, five conditions (`b0`, `b1`, `b2`, `b4`, `b8`), 984 slots per judge per condition, 9,840 judgment slots total.
- Primary contrasts D1, D2, D4, and D8 use common question-cluster bootstrap draws and Holm adjustment. S1 remains the separate b8-versus-b2 contrast.
- The tolerant parser is syntax-only. Strict INVALID remains wrong in the primary analysis. Question pre-screening remains prohibited.
- The request journal records every returned response, including visible empties, before downstream processing. Its live four-cell validation passed, and its dispatch/replay behavior does not need another paid test.

## Process reset for the main run

- Use one small manifest containing the Git commit, Python and dependency versions, linker or `not_applicable`, seeds, input and output SHA-256s, and GPU ordinal `not_applicable`.
- Checkout, build, provider, archive, and review-capacity preflight are retryable. Only the formal measurement is single-shot after analysis gates are fixed.
- An environmental or process interruption voids the formal measurement. Restart it with one log line and a fresh empty output store. Do not resume measurement rows from the interrupted run.
- Harness verification is one end-to-end seed rerun with a bit-identical raw output-store hash.
- Keep completed canary and validation archives read-only.

## Findings and continuation work

1. The journal validation exercised a safe call-boundary restart, not a real process death during dispatch. `KillSwitchClient` stops before the next provider call, after all prior responses have already reached the journal. This proves deterministic replay of completed calls, but it does not exercise the in-flight or ledger-settle-to-journal crash windows.

2. The prior `find_ambiguous_dispatches()` detected only a ledger `success` without a journal row and deliberately ignored an unmatched `reserved` event. The paid client writes `reserved` before dispatch. A real process death can therefore leave an open reservation after the request reached the provider, so the previous redispatch assumption was unsafe.

3. The journal layer now binds journalable ledger events to the exact request fingerprint and rejects or reports every unsafe surviving lifecycle state: `unknown_charge`, `charged_malformed`, unmatched `reserved`, success without journal bytes, duplicate success, and missing or mismatched fingerprint binding. Unkeyed events fail unless their attempt IDs are explicitly allowlisted for a separate preflight ledger. A durable execution-identity marker is fsynced before every provider-capable call and removed only after the response journal append is fsynced; any surviving marker blocks every wrapper on that path. A path-level operating-system lock spans refresh, dispatch, and append, so separately opened wrappers cannot sample concurrently. Journal loading now fails on schema/type violations, sequence gaps, duplicate keys, foreign identity, tampering, blank rows, and torn JSON.

4. The ratified process reset removes the need for cell-level recovery from those crash windows. The main runner must refuse resume after any environmental interruption and restart the whole formal measurement under a fresh identity and empty stores. Any later request for resumable formal measurement is a separate design change.

5. The sealed final canary cannot satisfy the old eight-block pace statistic retrospectively. Its reviewer index contains 576 unique rulings over 3.476 hours, which yields only three full one-hour blocks with counts `192, 128, 215`. The old eight-block L90 is therefore undefined. A three-block diagnostic under the same formula is 2,455.84 rulings per 24 elapsed hours, but it is not the declared gate. Under the process reset, review capacity is a retryable operational preflight. It still needs a representative workload definition before scheduling; merely leaving the clock running or replaying cached packets would not be valid evidence.

6. The active ask package previously requested an already-completed $2 validation and carried impossible hand-entered timestamps. It now records the completed result, accurately describes the call-boundary validation, and points chronology to the session event, Git commit, and result timestamp without modifying historical records.

## Acceptance gates before any main spend request

- Owner accepts the two-judge scope and the narrow claim language, or directs a scope change.
- Owner chooses how to resolve review capacity: approve a representative review-only load preflight, or replace the old eight-block operational rule with another explicit feasibility check. No retrospective padding.
- Main execution code uses the request journal, sets `halt_on_unknown_charge`, reconciles the complete ledger before provider construction or dispatch, and has no cross-process measurement resume path.
- The main runner holds one `RunLease` before journal opening and validates the chained ledger identity and contents in the same lease scope used for reconciliation.
- The main runner owns a run-level active marker from formal start through clean completion, so death before the first request or after a request marker clears still voids the identity.
- Focused tests cover visible-empty replay, request mismatch, every ambiguous ledger lifecycle, interruption refusal, and one-seed bit-identical harness output.
- Final item-6 methods review signs the analysis pins, interruption behavior, review-capacity evidence, and cost envelope.
- Owner authorization, if granted later, binds the exact main manifest and dollar cap. A discarded run requires a new authorization.
