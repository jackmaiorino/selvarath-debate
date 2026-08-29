# Phase-3 main-run ask package

Date: 2026-08-29. Prepared after the canary close-out (`2026-08-29-phase3-v3-successor-canary-closeout.md`) and its Codex review (`rejudge/phase3_v3_codex_closeout_consult_2026-08-29.md`). Nothing here authorizes spend; this is the checklist and the decision set.

## The ask, in one paragraph

A two-judge main run (Qwen3.8-2.4T, Llama-3.3-70B) over the frozen inventory of 9,840 judgment slots and 82 questions, at a recommended cap of $875 (planning mean $688 conservative-accounted plus a 25% governance reserve, per Codex). Launch is blocked until every item below is done. A discarded late identity is outside the reserve and would need its own reauthorization.

## Blocking checklist

| # | item | state |
|---|---|---|
| 1 | Owner methods review of the canary close-out and two-judge scope | WAITING ON OWNER |
| 2 | Request-level journal built and offline-validated | DONE 8/29: `rejudge/request_journal.py`; expanded focused tests cover replay, locking, strict persisted schema, accounted-client binding, and every ambiguous ledger lifecycle |
| 3 | Journal live validation on VS-019 plus controls | PASSED 8/29 (owner-approved, $0.32 actual of $2.00, zero uncertain): all 4 cells passed their frozen criteria. VS-019 b2 reproduced its empty-response truncation live twice; both empties were journaled and consumed by the retry-then-block ladder, and the cell completed with a parseable verdict. The validation establishes the returned-response journaling and replay behavior. Subsequent offline hardening added lifecycle reconciliation and path-level locking; it is not claimed to be the exact paid-tested revision. Results: `rejudge/phase3_v3_journal_validation_results_2026-08-29.json` |
| 4 | Eight-full-hour configuration-review windows (pace requirement) | NOT STARTED, owner-side scheduling |
| 5 | Tolerant-parser confirmation (frozen, deterministic, syntax-only) | DONE 8/29: the parser is the frozen protocol `parser_rule` regex `^ANSWER: ([AB])\.?$` after whitespace strip, pure and stateless; all 14 of Llama's strict-vs-tolerant capability divergences are the exact string `ANSWER: B.` (one trailing period). It cannot repair or reinterpret an answer, so pooled analysis is not blocked on this ground |
| 6 | Dedicated Codex methods consult on the main protocol | AFTER 1-5 |
| 7 | Owner spend authorization at $875 | LAST |
| 8 | Together billing reconciliation addendum | needs owner console pull, any time |

Chronology correction: Jack approved item 3 in the recorded orchestrator session at `2026-08-29T18:57:56Z`; the approval commit followed at `18:58:07Z`, and the result record was written at `19:03:54Z`. The manually entered `19:35Z` and `19:55Z` fields in the frozen plan and authorization records are timestamp errors. Use the session event, Git commit, and result-record timestamps for the execution chronology; the historical records remain unchanged.

## Item 3: live validation result ($2 cap)

Completed 8/29: the Codex review required the journal mechanism "exercised on VS-019 plus ordinary controls" with "crash/resume equivalence" demonstrated live before freezing. Offline tests prove the semantics; the live run proved them against the real provider, including VS-019's actual truncation behavior.

Design (frozen in the plan record): 4 cells live, using the EXACT terminal cell keys from records 006 and 008 (VS-019 b2 and b4, Qwen) plus one deterministic control per judge (SEL-010 b2 Qwen, CN-021 b2 Llama), transcripts preseeded byte-identically from the sealed canary archive. Each cell was stopped once at a deterministic call boundary, resumed, then fully re-driven over a poisoned client; pass criteria were zero re-dispatch of journaled requests, byte-identical records on re-drive, and correct ladder consumption of any journaled empty response. The reviewer was a constant ALLOW (disclosed; validation rows never enter analysis); the live Llama checker ran unmodified. This did not simulate a process death during a provider call or between ledger settlement and journal append. Offline reconciliation tests model the surviving ledger/journal states deterministically; no claim of a live in-flight kill is made.

Cost: $0.32164461 actual under the approved $2.00 aggregate cap, with zero uncertain spend. The run used its own ledger and archive. Validation rows are engineering artifacts and never enter any analysis.

## What the journal changes at main scale

The canary's three terminal cells all required an unmemoized degenerate response plus a mid-cell relaunch. The journal records every response (empties included) before the caller sees it and never re-dispatches a journaled request, so that mechanism is closed: a VS-019-class returned empty is consumed deterministically through the frozen retry-then-block ladder. Any provider-capable exception, open reservation, unknown charge, charged malformed response, or settled success without journal bytes makes the formal identity unusable. Under the ratified process reset, the whole interrupted formal measurement is void and restarts with a fresh identity and empty stores. There is no cell-level `ambiguous_dispatch` recovery path in the main design.

## Decisions the owner is being asked for now

1. Confirm the two-judge scope and the close-out (item 1), or direct changes.
2. Choose when to run the eight full one-hour configuration-review windows (item 4).
3. Provide the Together billing-console reconciliation data when convenient (item 8).

Item 3 is closed. After items 1 through 5 are complete, Codex performs the dedicated main-protocol review in item 6. Item 7 remains a separate exact owner authorization; no main-run provider call is currently authorized.
