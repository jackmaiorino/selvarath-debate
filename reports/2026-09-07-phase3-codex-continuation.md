# Phase 3 continuation, 2026-09-07

Jack asked Codex task `01a079a8-d267-7652-87e4-bd3c81870a37` to pick up the project
and ensure it continues. This continues his existing Phase 3 execution delegation and
the USD 1,100 cumulative stage cap. Codex is the acting operator for this continuation;
any delegated signature is produced by Codex, not personally by Jack or by Fable.
Signature disclosure: Codex generates the capacity and main authorization signatures
using Jack Maiorino's pinned key under his dated Phase 3 delegation. This records
delegated approval and does not assert Jack personally reviewed or signed each artifact.
Exact signed paths and byte hashes are recorded with the external launch package.

## Recovered state

The formal identity `phase3-main-34010df12dee7c38` stopped at 01:20:10 UTC after
2,460/10,332 rows and USD 24.89969640 settled spend, with zero unknown charges.
Amendment 15 fixes the reviewer receipt validation defect and counts that expenditure
against the successor's ceiling. Its archived rows and rulings remain evidence only.
No verdict content was used to make continuation decisions.

Fable's proposed successor at `b260c46` never launched. Its validation failed because
the capacity execution manifest binds `scripts/codex_reviewer_batch.py`, whose bytes
changed for the fix. This corrects the earlier consult brief's claim that the v6
capacity evidence could be reused after that change.

## Completed repair

Capacity v7 preserves the pinned CLI 0.149.0, reviewer model and provider profile,
concurrency, measurement gates, and 1,080-hour window. Its workload uses the v6 payload
line order with candidate contents exchanged between labels, and excludes every source
and v1-v6 prompt. It contains 365 eligible prompts, two disjoint 180-packet cohorts,
and five unused variants. Every v1-v6 derived summary still matches its frozen plan.
The incomplete builder's constant-zero empty-query check and incorrect archived
workload path are corrected. The launch input writer selects v7 evidence and its date.

Plan canonical SHA-256:
`34dbc0952b6afae0b442df53f0d8e3e1f1a0b54bfac9439552ba6900aa564630`.

## Accounting and launch

Prior reconciled spend is USD 119.27238490. Together with the voided main run, the
new main's maximum expenditure remains USD 955.82791870. Its USD 908.82 forecast
gives a projected cumulative stage total of USD 1,052.99208130. Capacity uses the
separately authorized ChatGPT reviewer login and makes no Together calls.

Capacity history contains 600 prior reservations, distinct from the voided main's
60 dispatches. A completed v7 cohort adds 180, making 840 prior external reviewer
dispatches in total. The ratified 59,040 reviewer gate applies to the exact main run;
only the USD 1,100 gate is explicitly cumulative across the stage. Capacity-plan
remaining-ceiling figures are bookkeeping and do not claim cumulative runtime enforcement.

The integrated checks, v7 capacity result, final-commit harness, exact signed launch
package, and live process/progress evidence will be recorded outside the source tree
under `E:/selvarath-archive`. Source changes stop before main measurement starts.
Neither the capacity result nor the main result is claimed complete by this report.
