# Astra Ultra code and amendment review: opinion (2026-10-18)

Brief: `2026-10-18-02-review-brief.md` (SHA-256 ec2d7f1c830819c601553c31499fb1e79b10b8461dd224af99537567c1b283cc, checked on the running machine). One-shot `codex exec -m gpt-6-astra -c model_reasoning_effort=ultra -s read-only`, codex-cli 0.159.2, brief on stdin from consults-20261018 on desktop-dj1c40r, codex session 01a14f10-6b1b-7fa3-8413-7743431c54cc. Opinion SHA-256 a408ffee8fe7e8b90e91552214a59e3896d660e3ab7d612601464cabde7fc1ac (as saved on that machine).

## Opinion (verbatim)

1. **Not ready to spend.** V3’s direction and the debate routing look sound. The analysis can currently produce misleading passes, especially after incomplete execution or failed compliance checks.

2. **Hardness-v3: conditional signoff.** It matches the summarized guidance; the original suggested text is unavailable for exact comparison. Strengthen item 2: “Neither conclusion nor an equivalent paraphrase should be explicitly stated; identifying the crux must still require inference using a relationship supported by the world.” Have the ledger quote that relationship’s support. Explicitly preserve all six task types. Keep this author guidance, without adding a retention criterion.

3. **Debate construction: signoff on the visible change.** `finalphase/debate.py:debate_task` routes each role through its model and effort setting on every request. Prompt construction is unchanged; subsequent histories naturally differ. Ordinary self-play IDs, token limits and record fields remain unchanged, preserving replay under the stated Store behavior.

4. **Blocking defect: `finalphase/probe.py:error_report`.** Groups and denominators come only from returned rows. One surviving group with 1/1 wrong can produce `"pass"` while the other group is absent; 11/110 also passes despite the declared 12/120 requirement. Empty results become `"fail"`. Require the complete, unique declared question × group × judge × order grid; otherwise report **incomplete**, not pass/fail/mixed.

5. **Blocking defect: `finalphase/asym.py:violation`, `run`.** Missing, empty or malformed compliance output becomes `False` and enters `pooled_honest_compliant`. Parse **yes/no/unknown** explicitly. A debate is confirmed honest-compliant only when every required honest turn has a valid negative check. Report unknowns separately.

6. **B’s interpretation is not implemented: `finalphase/asym.py:run`.** Honest-concession errors can carry a pairing across the raw threshold; the only filtered diagnostic pools pairings. Keep frozen raw scoring, but add per-pairing compliance and valid-verdict diagnostics and predeclare a separate confirmation-eligibility rule. The note’s unconditional “pass → freeze and confirm” conflicts with its later qualifications. Compliance exclusion is post-treatment selection: report it as sensitivity analysis, with its denominator and matching self-play subset.

7. **Baseline defect: `finalphase/asym.py:self_play_reference`, `run`.** A missing reference silently returns `{}` after spending; missing or duplicate reference cells are unchecked. Validate the expected question, debater, judge and order coverage before requests. Compare `haiku~fable` with Fable self-play and `luna~astra` with Astra self-play on matched cells. The current question/condition filtering is otherwise appropriate for the supplied format.

8. **Scoring contract/test gap: `finalphase/probe.py:error_report`; `tests/test_finalphase_probe.py:test_screen_needs_every_family_at_ten_percent`.** The helper counts only `not correct`; the fixture includes unparsed judgments marked correct. Given your `judge_task` assumption, this is not a demonstrated production scoring failure. Nevertheless, enforce or assert `not parse_ok ⇒ not correct` and fix the fixture. Add focused tests for incomplete grids, duplicates, unknown compliance and concession-driven crossings.

9. **Non-default input defect: `finalphase/asym.py:check_pairing`; `finalphase/debate.py:parse_debater`, `pair_label`.** `fable@garbage` passes validation; `fable@` versus `fable` passes despite equivalent settings; a colon in an effort string corrupts downstream ID parsing. Validate supported efforts, canonicalize settings and reject reserved delimiters. The two fixed default pairings are unaffected.

10. **Amendment corrections.** Shared seed hints do not establish that realized rounds differ only in the author block; describe matched author configurations with fresh generation and retention. Replace “prior traps bought ambiguity” with the observed association between errors and determinacy flags. Call the Phase 2 subset “validator-clean” to distinguish it from full pipeline eligibility. “Cheapest current tier” and actual model-version mappings are not established by this diff. The remaining reading is sound: exploratory screens, fresh confirmation after freezing, clustering acknowledged, A preferred if both qualify, and no small-budget-peak claim.

## Disposition

All ten points were applied in the next commit: the screen needs the full declared grid and reports incomplete otherwise, counts against the declared denominator and asserts that unparsed verdicts score wrong; compliance is yes/no/unknown and a debate is honest-compliant only when every honest turn has a valid negative check; per-pairing diagnostics, matched self-play comparison and a pre-declared confirmation-eligibility rule; the self-play baseline is checked for exact coverage before any request; debater specs are validated and canonicalized; the v3 block and the note carry the requested wording.
