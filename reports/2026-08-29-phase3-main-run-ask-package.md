# Phase-3 main-run ask package

Date: 2026-08-29. Prepared after the canary close-out (`2026-08-29-phase3-v3-successor-canary-closeout.md`) and its Codex review (`rejudge/phase3_v3_codex_closeout_consult_2026-08-29.md`). Nothing here authorizes external execution, reviewer dispatch, provider calls, or spend; this is the checklist and decision set.

Current-state update, 2026-08-30: commits through `acee8b4` add item 7's blocked driver,
finalization, analysis handoff, provider and reviewer provenance, and crash-consistent reviewer
closeout. The fake-only two-execution harness receipt has passed from `acee8b4`; external
authority and fresh evidence remain. See
`reports/2026-08-30-phase3-main-offline-closure.md` for the current gate order. The historical
checklist below is retained to preserve the decision chronology.

## The ask, in one paragraph

This package describes a possible main run using `Qwen/Qwen3.8-2.4T-A95B` and `meta-llama/Llama-3.3-70B-Instruct-Turbo` over 82 questions and 9,840 judgment slots. The $875 figure is a proposed planning cap from the sealed canary, not a current authorization or certified ceiling. The final cap requires the fresh forecast, price snapshot, billing reconciliation, final manifest, and owner authorization. Any voided formal identity requires separate reauthorization.

## Blocking checklist

| # | item | state |
|---|---|---|
| 1 | Owner confirmation of the two-judge scope and narrow claim boundary | DONE 8/29: Jack confirmed the two named endpoint-specific judges and narrow claim boundary; decision record: `rejudge/phase3_main_scope_capacity_decision_2026-08-29.json` |
| 2 | Request-level journal built and offline-validated | DONE 8/29: `rejudge/request_journal.py`; expanded focused tests cover replay, locking, strict persisted schema, accounted-client binding, and every ambiguous ledger lifecycle |
| 3 | Journal live validation on VS-019 plus controls | PASSED 8/29 (owner-approved, $0.32 actual of $2.00, zero uncertain): all 4 cells passed their frozen criteria. VS-019 b2 reproduced its empty-response truncation live twice; both empties were journaled and consumed by the retry-then-block ladder, and the cell completed with a parseable verdict. The validation establishes returned-response journaling and completed-call replay at controlled call boundaries only. Subsequent offline hardening added lifecycle reconciliation and path-level locking; it is not claimed to be the exact paid-tested revision. Results: `rejudge/phase3_v3_journal_validation_results_2026-08-29.json` |
| 4 | Representative review-only capacity preflight | OFFLINE DESIGN DONE, EVIDENCE NOT RUN: two frozen disjoint 180-packet cohorts, each split into three 60-packet waves. The pass contract certifies 1,440 valid rulings per 24 hours against 1,312 required. Cohort 2 is available only after a receipt-bound interruption record and operator attestation classify cohort 1's cause as environmental; this is not independent cause proof. Within the trusted-local history contract, completed failure is terminal. No reviewer dispatch or external execution is authorized |
| 5 | Tolerant-parser confirmation (frozen, deterministic, syntax-only) | DONE 8/29: the parser is the frozen protocol `parser_rule` regex `^ANSWER: ([AB])\.?$` after whitespace strip, pure and stateless; all 14 of Llama's strict-vs-tolerant capability divergences are the exact string `ANSWER: B.` (one trailing period). It cannot repair or reinterpret an answer, so pooled analysis is not blocked on this ground |
| 6 | Frozen main analysis pins and reporting engine | DONE OFFLINE 8/29: common world-cluster draws, D1/D2/D4/D8, S1, Holm, descriptive sup-t, strict INVALID and sensitivity reports, frozen capability anchors, endpoint-specific output, and fail-closed integrity checks have synthetic and adjacent test evidence. Nonempty exclusions remain blocked pending production finalization admission |
| 7 | Production main driver, finalization, and one-seed harness | OPEN: no live factory or CLI exists. Manifest and authorization validation, exact transcript and judgment partition, terminal/context evidence, final reconciliation, archive and identity protection, output hashes, and a bit-identical seed rerun remain required |
| 8 | Together billing reconciliation addendum | NEEDS OWNER CONSOLE PULL, any time |
| 9 | Dedicated Codex methods consult on the complete main package | BLOCKED until capacity evidence passes and items 7 and 8 make the production driver, finalization, harness, cost envelope, and analysis integration reviewable |
| 10 | Owner authorization bound to the exact final manifest and refreshed dollar cap | LAST; $875 is the current proposal only |

The capacity history, interruption evidence, and retained receipts are consistency controls on a trusted local filesystem. They are not tamper-proof against coordinated replacement of all three.

Chronology correction: Jack approved item 3 in the recorded orchestrator session at `2026-08-29T18:57:56Z`; the approval commit followed at `18:58:07Z`, and the result record was written at `19:03:54Z`. The manually entered `19:35Z` and `19:55Z` fields in the frozen plan and authorization records are timestamp errors. Use the session event, Git commit, and result-record timestamps for the execution chronology; the historical records remain unchanged.

## Item 3: live validation result ($2 cap)

Completed 8/29: the Codex review required the journal mechanism to be exercised on VS-019 plus ordinary controls. Offline tests prove the semantics; the live run proved returned-response persistence and replay after deterministic stops before the next provider call, including VS-019's actual truncation behavior. It did not demonstrate process-death crash equivalence or exercise the in-flight and ledger-settle-to-journal windows. Later offline reconciliation tests fail closed on those surviving states.

Design (frozen in the plan record): 4 cells live, using the EXACT terminal cell keys from records 006 and 008 (VS-019 b2 and b4, Qwen) plus one deterministic control per judge (SEL-010 b2 Qwen, CN-021 b2 Llama), transcripts preseeded byte-identically from the sealed canary archive. Each cell was stopped once at a deterministic call boundary, resumed, then fully re-driven over a poisoned client; pass criteria were zero re-dispatch of journaled requests, byte-identical records on re-drive, and correct ladder consumption of any journaled empty response. The reviewer was a constant ALLOW (disclosed; validation rows never enter analysis); the live Llama checker ran unmodified. This did not simulate a process death during a provider call or between ledger settlement and journal append. Offline reconciliation tests model the surviving ledger/journal states deterministically; no claim of a live in-flight kill is made.

Cost: $0.32164461 actual under the approved $2.00 aggregate cap, with zero uncertain spend. The run used its own ledger and archive. Validation rows are engineering artifacts and never enter any analysis.

## What the journal changes at main scale

The canary's three terminal cells all required an unmemoized degenerate response plus a mid-cell relaunch. The journal primitive closes completed-request redispatch for compliant callers on the journaled path: a VS-019-class returned empty is persisted and consumed deterministically through the frozen retry-then-block ladder. That property is not yet established for a main-scale production driver because no live driver or provider factory is integrated. The production driver must preserve one journaled path, treat any provider-capable exception, open reservation, unknown charge, charged malformed response, or settled success without journal bytes as fatal to the identity, and prohibit cell-level recovery. An interrupted formal measurement restarts only with a fresh manifest, identity, artifact root, empty stores, and separate authorization.

## Owner decisions recorded 8/29

Jack replied `Confirmed, continue` after Codex stated the exact interpretation: confirm Qwen/Qwen3.8-2.4T-A95B and meta-llama/Llama-3.3-70B-Instruct-Turbo as the two endpoint-specific judges, and replace the obsolete eight-full-hour pace rule with a representative review-capacity preflight. This confirmation grants no external execution, reviewer dispatch, provider-call, or spend authority. The decision is recorded in `rejudge/phase3_main_scope_capacity_decision_2026-08-29.json`.

The immediate owner-supplied data item is the Together billing-console reconciliation in item 8. Separate owner authority will also be required before capacity measurement and, later, before any main provider call. The offline capacity plan, runner safety foundation, and analysis pins and engine now exist. Capacity evidence, the live production driver, production exclusion admission, the one-seed harness, the main report, and the dedicated methods review remain incomplete.

Items 1, 2, 3, 5, and 6 are closed, with items 2 and 6 closed only at their stated offline scope and item 3 limited to its controlled call-boundary live validation. Item 4 remains open until separately authorized representative evidence passes. Item 7 is production implementation, not a claim that the fake-only foundation is launch-ready. Item 9 remains blocked by the live production and cost gates. Item 10 remains a separate exact owner authorization. No capacity dispatch, main provider call, or main spend is currently authorized.
