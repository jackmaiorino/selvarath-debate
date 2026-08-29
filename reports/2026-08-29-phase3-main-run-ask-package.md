# Phase-3 main-run ask package

Date: 2026-08-29. Prepared after the canary close-out (`2026-08-29-phase3-v3-successor-canary-closeout.md`) and its Codex review (`rejudge/phase3_v3_codex_closeout_consult_2026-08-29.md`). Nothing here authorizes spend; this is the checklist and the decision set.

## The ask, in one paragraph

A two-judge main run (Qwen3.8-2.4T, Llama-3.3-70B) over the frozen inventory of 9,840 judgment slots and 82 questions, at a recommended cap of $875 (planning mean $688 conservative-accounted plus a 25% governance reserve, per Codex). Launch is blocked until every item below is done. A discarded late identity is outside the reserve and would need its own reauthorization.

## Blocking checklist

| # | item | state |
|---|---|---|
| 1 | Owner methods review of the canary close-out and two-judge scope | WAITING ON OWNER |
| 2 | Request-level journal built and offline-validated | DONE 8/29: `rejudge/request_journal.py`, 9 tests incl. crash/resume bit-identical replay and ambiguous-dispatch detection |
| 3 | Journal live validation on VS-019 plus controls | PASSED 8/29 (owner-approved, $0.32 actual of $2.00, zero uncertain): all 4 cells passed all criteria. VS-019 b2 reproduced its empty-response truncation live TWICE; both empties were journaled, consumed by the retry-then-block ladder, and the cell completed with a parseable verdict; both VS-019 terminal cell keys resumed after kills and replayed byte-identically with zero provider calls. Results: `rejudge/phase3_v3_journal_validation_results_2026-08-29.json`. The journal mechanism is now validated and freezes as-is |
| 4 | Eight-full-hour configuration-review windows (pace requirement) | NOT STARTED, owner-side scheduling |
| 5 | Tolerant-parser confirmation (frozen, deterministic, syntax-only) | DONE 8/29: the parser is the frozen protocol `parser_rule` regex `^ANSWER: ([AB])\.?$` after whitespace strip, pure and stateless; all 14 of Llama's strict-vs-tolerant capability divergences are the exact string `ANSWER: B.` (one trailing period). It cannot repair or reinterpret an answer, so pooled analysis is not blocked on this ground |
| 6 | Dedicated Codex methods consult on the main protocol | AFTER 1-5 |
| 7 | Owner spend authorization at $875 | LAST |
| 8 | Together billing reconciliation addendum | needs owner console pull, any time |

## Item 3: the live validation run ($2 ask)

Purpose: the Codex review requires the journal mechanism "exercised on VS-019 plus ordinary controls" with "crash/resume equivalence" demonstrated live, before freezing. Offline tests prove the semantics; the live run proves them against the real provider, including VS-019's actual truncation behavior.

Design (frozen in the plan record): 4 cells live, using the EXACT terminal cell keys from records 006 and 008 (VS-019 b2 and b4, Qwen) plus one deterministic control per judge (SEL-010 b2 Qwen, CN-021 b2 Llama), transcripts preseeded byte-identically from the sealed canary archive. Each cell is killed mid-flight once, resumed, then fully re-driven over a poisoned client; pass criteria are zero re-dispatch of journaled requests, byte-identical records on re-drive, and correct ladder consumption of any journaled empty response. The reviewer is a constant ALLOW (disclosed; validation rows never enter analysis); the live Llama checker runs unmodified. The settle-then-crash ambiguity window is validated offline only (deliberately inducing it against live billing is not reliably possible); the offline test covers it deterministically.

Cost: per-slot actuals from the canary put the 4 cells near $0.20; kills add partial re-execution. Ask: $2.00 aggregate cap, $1.00 uncertain ceiling, its own ledger and archive. Validation rows are engineering artifacts and never enter any analysis.

## What the journal changes at main scale

The canary's three terminal cells all required an unmemoized degenerate response plus a mid-cell relaunch. The journal records every response (empties included) before the caller sees it and never re-dispatches a journaled request, so that mechanism is closed: a VS-019-class cell now completes deterministically through the frozen retry-then-block ladder instead of being excluded. The residual loss class is the narrow settle-then-crash window, which is detected at resume and resolved INVALID (bounded, counted) rather than re-dispatched. The main protocol must add that `ambiguous_dispatch` disposition; it goes into the item-6 consult.

## Decisions the owner is being asked for now

1. Approve the $2.00 live validation run (item 3).
2. Confirm the two-judge scope and the close-out (item 1), or direct changes.
3. Tell me how you want to schedule the eight-hour review windows (item 4).

Everything else proceeds without further input until item 7.
