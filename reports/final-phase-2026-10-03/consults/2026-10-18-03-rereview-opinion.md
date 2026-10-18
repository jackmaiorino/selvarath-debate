# Astra Ultra re-review: opinion (2026-10-18)

Brief: `2026-10-18-03-rereview-brief.md` (SHA-256 be7cce16d15d79952291ebbc985e8e686c27657f738e79bcc590304a9c1eb25e, checked on the running machine). Same method as consult 2, codex session 01a14f17-113e-7a10-90cb-b52043e69b14. Opinion SHA-256 875cc37f6bb2395f00df03483a85d080b5cd3f71f108f7adab35f2f0638e25e5 (as saved on that machine).

## Opinion (verbatim)

1. **Partly.** Overall readiness improved: A is ready; B still has compliance and eligibility defects.

2. **Resolved.** V3 requires inference, quotes support for the decisive relationship, preserves all six task types, and adds no retention criterion.

3. **Resolved.** Role-specific model/effort routing preserves the stated self-play behavior.

4. **Resolved.** `error_report` checks the complete declared grid and reports missing, duplicate, undeclared or empty results as incomplete.

5. **Partly.** Tri-state parsing is present, but unknown checks can disappear behind violations, and contradictory output can be accepted as compliant. See 11–12.

6. **Partly.** Per-pairing diagnostics, matched sensitivity analysis and an explicit eligibility rule are present. Eligibility still has defects. See 11 and 13.

7. **Resolved.** Required baseline cells are validated before requests; the default pairings receive the correct matched self-play comparisons.

8. **Resolved.** The scoring assertion, corrected fixture and requested focused tests are present. The reported 100 passing tests do not cover the remaining counterexamples below.

9. **Resolved.** The asymmetric entry point validates efforts, canonicalizes defaults, rejects delimiters and rejects duplicate pairings.

10. **Partly.** The methodological wording is corrected. Exact model-version mappings and relative prices remain unverified from the supplied material.

11. **New defect: `finalphase/asym.py:debate_status`, `pairing_diagnostics`.** `["yes", "unknown"]` becomes only `"violation"`, hiding the unknown check. One such debate plus 19 compliant debates with 12/114 errors produces eligibility despite the no-unknown rule. Track **any violation** and **any unknown** independently.

12. **New defect: `finalphase/asym.py:compliance_verdict`.** `"VIOLATION: no\nVIOLATION: yes"` returns `"no"`. Contradictory malformed output therefore enters the compliant subset. Require one unambiguous verdict field; otherwise return unknown.

13. **New defect: `finalphase/asym.py:pairing_diagnostics`.** Eligibility uses the observed denominator: one returned wrong judgment can produce `confirmation_eligible=True`. Require complete per-pairing coverage and the declared 12/120 threshold. The top-level incomplete screen prevents an overall pass if the written decision rule is followed, but the eligibility field remains false.

14. **Spending signoff, conditional on the owner’s go:** **A, hardness-v3, about $50: yes**, for the stated exploratory run and caps. **B, asymmetric, about $20: no**, pending fixes to 11–13 and focused regression checks. This review is based solely on the supplied brief and diff.

## Disposition

11 to 13 fixed in the next commit with regression tests: violation and unknown are tracked independently per debate; a compliance output needs exactly one yes/no VIOLATION field; eligibility needs the full declared per-pairing grid once each and 12 of the declared 120. For 10, the model mapping and list prices from `finalphase/models.py` go into the next brief.
