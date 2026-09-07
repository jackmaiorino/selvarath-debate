# Phase 3 main: identity 34010df1 void and corrective successor (2026-09-07)

Orchestrator: Claude Code session f817b4ab under Jack Maiorino's written Phase 3 delegation.
Companion records: `rejudge/phase3_main_amendment_15_predecessor_void_2026-09-07.json`,
`rejudge/phase3_main_predecessor_void_accounting_2026-09-07.json` (pinned by raw SHA-256 in
`rejudge/phase3_main_runtime_policies.py`), and the Codex consult
`rejudge/phase3_main_codex_successor_consult_2026-09-07.md`.

## What happened

Identity `phase3-main-34010df12dee7c38` (commit 2707cd3) started its formal measurement at
2026-09-06T19:53:25Z. Pass 1 completed 2,460 of 10,332 rows with 2,029 settled Together calls
($24.90, zero unknown charges). Reviewer wave 1 (60 packets) dispatched under the dispatch guard
and every ruling was written. At 01:20:10Z the driver exited with:

```
REFUSED: Phase3MainLiveError: reviewer invocation evidence failed for bd3f5833...:
reviewer dispatch guard evidence no longer verifies exactly
```

## Root cause

`validate_invocation_evidence` in `scripts/codex_reviewer_batch.py` re-evaluates the dispatch
guard snapshot after a wave. It passed the CLI path, model, effort, concurrency, packet
directory, and the recorded check time, but not the invocation's recorded model provider
profile. The v6 capacity plan's reviewer configuration carries that profile, so the guard
evaluator raised "capacity model provider profile drifted" for every receipt. The dispatch-time
evaluation and the batch preflight both pass the profile; only the post-wave recheck omitted it.

Reproduced offline: the identical recheck without the profile returns that error; with the
profile it returns verified true and evidence byte-identical to the dispatch-time record.
Nothing environmental changed: pinned CLI wrapper hash, host, and source tree were unchanged.
The gap was never exercised because the capacity preflight carries no dispatch guard and the
provenance test fixture's plan carried neither a profile nor the legacy transport flag.

## Decisions (Codex consult, gpt-6-astra, one-shot)

- Fix approved with negative regression coverage. Tests now carry the real v6 profile, two
  negatives reproduce the failure message, and an explicit-false legacy flag is covered.
- Fresh-identity route approved on condition that the successor's ceiling is enforced, not
  described. The pinned void accounting record is validated against the archived predecessor
  ledger at load; the provider client starts at prior reconciled plus voided spend, so it
  refuses any reservation past $955.82791870; finalization accounting and the signed
  authorization text carry the same figures.
- The environmental-successor manifest mode is a latent deadlock under the console billing
  policy (it demands provider-authenticated settlement and its reason codes exclude code
  defects). Recorded as a known limitation, not fixed before the successor.
- The delegation covers a corrective relaunch under the unchanged $1,100 cap. The signature
  disclosure says so and does not portray the fix as the owner's personal ratification.
- Predecessor rows and rulings are evidence only. No verdict content was inspected.

## Accounting

| Item | USD |
|---|---:|
| Prior reconciled (18 ledgers, unchanged) | 119.27238490 |
| Voided predecessor, settled | 24.89969640 |
| Successor certified forecast | 908.82 |
| Expected stage total | 1052.99208130 |
| Stage cap | 1100.00 |
| Maximum successor expenditure (enforced) | 955.82791870 |

The 2026-09-04 billing reconciliation and the certified forecast are unchanged and do not
incorporate the voided spend; it is disclosed as stage expenditure outside the reconciled
segments.

## Successor launch

Filled in by the execution log below once the successor identity starts.

## Execution log

- 01:19Z monitor reported the refusal; 01:20Z driver exit confirmed; launch record and note
  of the predecessor updated with a termination section.
- 01:35Z root cause reproduced offline against the failing receipt.
- 02:10Z Codex consult; 02:45Z fix and regression tests (provenance suite 50 passed).
- 03:20Z amendment 15 and the pinned void accounting record; driver, finalization, and
  authorization text wired; affected suites 351 passed; full suite started on E:.
