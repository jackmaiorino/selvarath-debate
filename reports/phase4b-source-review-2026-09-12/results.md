# Phase 4B amended AI source review

Jack approved substituting Codex AI source review for the planned human check after the blinded adjudication completed. The [amendment](../../rejudge/phase4b_ai_review_amendment_2026-09-12.json) records his exact instruction and binds the original protocol, inputs, results and fixed sample. The original protocol and pre-amendment archive remain unchanged.

## Review result

All **20 predetermined cases** were checked against all three full frozen source documents and the original oracle contract. The review accepts **15 proposed labels** and excludes **five corrections as AMBIGUOUS**, cases **2, 6, 8, 14 and 15**. No replacement sample or alternative label was introduced. Every reviewed case has a source-based reason; all 34 quoted passages were verified as exact source substrings.

The five exclusions concern uncertain attribution to an institution, an interpretive regional description, purchases inferred from market pricing, territorial versus operational control, and a vague description of dispute management. They are exclusions for semantic uncertainty, not five demonstrated factual errors. The NOT ADDRESSED judgment on case 17 is retained, with its reasoning corrected to distinguish a necessary condition from a sufficient or sole condition.

A separate Codex pass formed judgments from the claims and full sources before opening adjudicator judgments. The primary review had previously seen excerpts for cases 1, 2 and 20. Neither review used per-case original oracle labels, donor identities, candidate answers or recipient outcomes. Both passes are AI from the same model family. This review does not supply independent human validation or independent model-family validation.

The amended offline consensus has released the candidate repair-label map. Independent recomputation matches the coverage below; the map differs from the preliminary consensus only by those five exclusions. All 51 focused tests passed, including preservation of human-mode behavior and truthful AI provenance.

## Coverage after exclusions

| Measure | Before review | After review | Required minimum |
|---|---:|---:|---:|
| Distinct changed claims | 554 | 549 | 20 |
| Changed histories | 591 / 1,312 | 588 / 1,312 | 40 |
| Questions containing changes | 80 / 82 | 80 / 82 | 10 |
| Changed answered exchanges | 773 | 761 | n/a |
| Changed immediate/repeated label spans | 3,952 | 3,896 | n/a |

The coverage minima still pass. The candidate changes reach 44.82% of histories; this is coverage, not an observed repair benefit or a power guarantee. The five excluded claims add to the 802 adjudication exclusions, leaving 807 ambiguous/excluded claims overall. Their original labels remain unchanged wherever they occur.

The review and amended offline analysis made no provider calls. The original adjudication cost remains $4.85184842 actual plus $0.09712920 retained uncertainty. The future 5,248-call original/repaired recipient comparison remains separately budgeted and has not started. No Qwen/Llama repair benefit or explanation of their difference has yet been measured.

The private completed review is `E:\selvarath-archive\phase4b-blinded-adjudication-2026-09-12\ai-source-review-2026-09-12\source_review_completed.md`. The corresponding JSON contains actual AI decisions and provenance. [summary.json](summary.json) contains aggregate coverage and hashes, without raw claims or sources.
