# Consult #28: frozen no-query transition

## Ruling

Replace the provisional sentence. Freeze this exact UTF-8 payload:

> No factual verification was performed for this query. This message contains no evidence about the world document. The query slot has been consumed.

The prior wording, “No verification result is available for this query,” was truth-neutral
and did not reveal the checker, reviewer, or clean/placebo mode. It nevertheless left two
important ambiguities:

1. “is available” could be read as a temporary outage, a pending result, or an oracle
   `NOT ADDRESSED` response rather than a policy block with no dispatch; and
2. it did not state the load-bearing difference from the frozen free-retry transition: this
   path consumes the query slot.

The replacement states only runtime facts. No oracle or placebo responder was dispatched for
the blocked query, the message supplies no evidence about the world document, and the slot is
consumed. It gives the judge no checker verdict, reviewer verdict, gate identity, arm identity,
or claim-truth signal.

## Scope of the freeze

The exact same bytes must be used for every consumed, non-dispatched query in both clean and
placebo mode:

- immediate feedback after the blocked query;
- the prior-query summary shown before a later query slot;
- the final `VERIFICATION RESULTS` block shown before the verdict; and
- a `batch_same_qa_b2` replay of the sequential judgment.

The frozen free-retry payload remains unchanged. It is shown only after the first contract
rejection, when the same slot may be retried. Checker outage, malformed checker output, and
unresolved checker output halt the cell and must never be converted into this transition.

## Required binding

The append-only machine-readable decision
`rejudge/phase2_no_query_transition_2026-07-26.json` is the source of truth. The canary
composition code must load that artifact and verify its declared UTF-8 SHA-256 before returning
the payload. The successor canary manifest must bind both the artifact’s canonical JSON hash
and the payload hash.

This ruling is a protocol-text disposition under the owner’s scoped 2026-07-24 delegation. It
grants no execution authority, changes no cap, and does not authorize the canary or main run.
The prior canary execution identity
`6c27ffe5e4ebe9c4f73de638e66b143f25ebd00764325c1d881ef99d1827c318`
must remain unauthorized and be superseded by a newly materialized manifest after the binding
and its tests are complete.

