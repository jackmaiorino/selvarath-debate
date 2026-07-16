# DRAFT — Project Update and Pilot Correction (Not Posted)

**Status:** local draft only; requires author/lead approval before publication.

We have completed the clean validation of our result that limited oracle access can worsen
debate-based oversight, and the result survives in a smaller form.

The original pilot estimated that moving from no oracle to a few oracle calls increased judge
error by 7.2 percentage points (95% CI 4.6 to 10.2). A code audit then found two material bugs in
the pilot oracle channel: `NOT ADDRESSED` responses were miscoded as `NO`, and the oracle-facing
query text was malformed. Those bugs invalidate the pilot's mechanism-level interpretation and
require a correction to the original write-up.

The audit also documented measurement limitations in verdict/confidence parsing and A/B assignment,
plus a debate-generation issue: each debater had advance knowledge of the opponent's upcoming case.
The Stage 1 comparison re-judges fixed legacy transcripts and therefore does not repair that debate
contamination; Phase 2 will regenerate every debate with genuinely blind opening turns. The full
known-bug provenance should be linked from the published correction.

We rebuilt the judging harness and ran a pre-registered validation experiment totaling 11,330
judgments across clean and diagnostic arms. The corrected estimate in the clean comparison is a
3.4-point increase in error (95% cluster-bootstrap CI 1.3 to 5.9). Thus the qualitative harm signal
remains, but the bugs explain roughly half the original magnitude. The pilot's apparent U-shaped
recovery at larger budgets did not reproduce. Extra deliberation turns without real evidence also
caused a smaller degradation.

Follow-up mechanism work changed our interpretation further. Harmful flips came from both oracle
mistakes and judges over-reading valid oracle answers. More importantly, replaying the exact same
query/answer evidence in a fresh, neutral batch format removed a large share of the harm. This
is consistent with effects from conversational presentation, turn structure, or seeing its own
prior questions, rather than bad factual content alone.

We also completed a held-out calibration for the next experiment. The proposed task uses fresh
blind, uncapped, three-round debates. The proposed judge roster is Qwen2.5-7B, Gemma-4-31B,
Llama-3.3-70B, and GPT-OSS-120B; debaters are Llama-3.3-70B and hosted Qwen3.7-Plus; the oracle is
Llama-3.3-70B. Eleven Gemma calibration cells had provider failures (10 timeouts and one HTTP 500)
and are explicitly unresolved.

The paid Phase 2 main run has not started and is not authorized. Before launch we are freezing the
three primary tests, capability measurement, a cap-protection secondary, the query-screening rule,
artifact storage, and a cumulative spending boundary. The current offline plan contains 82 main
questions, 492 new debate transcripts, and 18,696 judgments. That is a reviewable base inventory,
not a frozen launch count: empty-evidence controls, full-document ceiling anchors, and a matched
legacy bridge still need explicit include-or-drop decisions.

Suggested source files are listed below. Before posting, replace these repo-relative paths with
immutable public URLs pinned to the approved Git commit or release tag:

- Stage 1 report: `reports/2026-07-09-stage1-rejudge-results.md`
- Mechanism memo: `reports/2026-07-12-mechanism-and-packaging-memo.md`
- Calibration report: `reports/2026-07-14-calibration-results.md`
- Readiness/sign-off record: `docs/phase2-readiness-and-signoff.md`
