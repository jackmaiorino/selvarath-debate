# Manifund update: Phase 2 result and erratum, Phase 3 main run live

Draft 2026-09-06, revised after a one-shot Codex review (see `2026-09-06-codex-review.md`).
Not posted; Jack posts. Branch `codex/phase3-v3-successor` was pushed to the
fork on 2026-09-06 and all links returned HTTP 200. Spend figures are as of 2026-09-06 23:50 UTC.
Everything below the line is the posted text.

---

## What progress have you made since your last update?

This update is late: Phase 2 finished on August 11 and a correction followed on August 21.
Both are reported here for the first time.

The Phase 2 design posted on July 18 ran to completion: 22,140 judgments, 100% complete,
$172.86 against a $173.80 projection. The original analysis was pinned before unblinding and
run once.

**Result.** In our three fictional worlds, giving the judge a two-call oracle budget during
sequential debate, versus the same debate with no oracle, increased judge error. Under the label
assignment actually run, the estimate is +3.9 percentage points (95% CI [2.2, 5.7], Holm
p = 0.0006).

**Erratum (August 21).** The Phase 3 canary exposed a harness defect: the protocol's
mirrored-sides replicate was never implemented, so both replicates of every judgment showed the
correct answer in the same position, and the side-bias gate as computed was non-diagnostic. We
withdraw "fully pre-registered mirrored result" and "all eligibility gates passed". Under a
reanalysis specification and decision rule frozen before any side-stratified number was
computed, the headline survives as post-hoc robust: +4.56pp after 50/50 side standardization
(CI [2.89, 6.25]), with the valid-only and within-question estimates agreeing in direction.
These intervals are question-cluster bootstraps under the observed assignment and do not
capture uncertainty over the missing opposite-polarity outcomes. The evidence-content contrast
is stable; the packaging contrast is positive under the strict rule but inconclusive under
valid-only. The secondary contrasts are re-examined in the report.

**Phase 3 launched on September 6.** It reuses the 492 blind transcripts across oracle budgets
0, 1, 2, 4, and 8 with two judge endpoints, Qwen3.8-2.4T-A95B and Llama-3.3-70B, for 9,840
judgments: four budget-versus-zero error contrasts under Holm, plus a secondary comparing
budget 8 with budget 2. Screening reduced seven candidate judges to two, so the confirmatory
claim averages these two fixed endpoints equally, with no capability-slope inference and no
generalization to scale.

**Delegation disclosure.** While I was away, Claude launched the run under my written Phase 3
spending and execution delegation and generated the authorization signature using my key. The
record identifies this as delegated approval and does not assert my personal review or signing
of that artifact.

**Spend.** About $208 through July 14 and $181 for Phase 2. Phase 3 pre-main accounting is
$119.27 ($95.68 settled, $23.59 uncertain reservations); Together's console shows $94.07 for the
same window. The main run has spent about $17 so far. The $1,100 Phase 3 cap includes the
pre-main spending; the certified main-run forecast is $908.82.

- Phase 2 report with erratum: https://github.com/jackmaiorino/selvarath-debate/blob/34898fb/reports/2026-08-11-phase2-main-results.md
- Phase 3 protocol: https://github.com/jackmaiorino/selvarath-debate/blob/2707cd3/rejudge/phase3_protocol_v3_r6.json
- Delegated signature record: https://github.com/jackmaiorino/selvarath-debate/blob/2707cd3/rejudge/phase3_main_delegated_signature_provenance_2026-09-06.json

## What are your next steps?

Results or a status update in about a week, either way, under the frozen analysis.

## Is there anything others could help you with?

Methods scrutiny of the erratum's post-hoc side standardization and of the two-endpoint claim
language for Phase 3.
