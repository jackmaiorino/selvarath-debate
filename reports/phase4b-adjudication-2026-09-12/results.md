# Phase 4B blinded adjudication results

**Subsequent amendment:** Jack approved Codex AI review in place of the human check. The fixed sample is now reviewed, with five corrections excluded and coverage still sufficient. See the [amended review result](../phase4b-source-review-2026-09-12/results.md). The counts below preserve the pre-review snapshot.

The audit completed **9,664 of 9,664 requests** on September 12, 2026 at 11:38 a.m. Eastern. Its proposed label changes cover **554 distinct claims in 591 of 1,312 histories (45.05%), spanning 80 of 82 questions**. This passes the declared coverage minima. The fixed 20-claim human source review is pending, so no repair labels have been released and recipient evaluation has not started.

These are candidate corrections under the frozen label contract. The audit has not established that the corrections improve either recipient or explain the Qwen/Llama difference observed in 4A.

## Consensus and coverage

Both adjudicators reviewed all 4,832 exact world/claim identities. They agreed with valid source quotations on 4,030 claims: 3,268 YES, 48 NO and 714 NOT ADDRESSED. The remaining **802 claims (16.60%)** are excluded: 376 label disagreements and 426 with at least one ineligible adjudication. Their original labels stay unchanged. Exact quotations establish reference integrity, not semantic correctness; two agreeing models can share an error.

The 554 proposed changes affect 773 of 8,125 answered exchanges and 3,952 of 44,604 immediate or repeated label spans. All 1,704 blocked exchanges are preserved. Required minima were 20 changed claims, 10 questions and 40 histories. Coverage supplies room for an effect, not a power guarantee: 591/1,312 is an upper bound on mean expected causal benefit from these edits, not a predicted benefit.

| Source or world | Changed histories | Changed claims | Excluded claims / distinct claims |
|---|---:|---:|---:|
| Llama donor | 270 / 656 | 219 | 367 / 2,423 |
| Qwen donor | 321 / 656 | 361 | 472 / 2,737 |
| Carath Norn | 181 / 432 | 161 | 219 / 1,443 |
| Selvarath | 216 / 448 | 190 | 337 / 1,671 |
| Vethun Sarak | 194 / 432 | 203 | 246 / 1,718 |

Claims can recur across donor histories and questions, so donor and question claim counts are not additive. These descriptive differences are not tested recipient effects or estimates of oracle error prevalence. Aggregate coverage for each question is in [summary.json](summary.json).

## Completion and cost

The final JSONL exactly matches the durable SQLite results and frozen request panel. An independent audit verified request identities, first successful completions, usage and price calculations. No duplicate successful cells, active requests, pending retries or fatal latch remained. Eleven main HTTP 503 attempts and one preflight HTTP 503 attempt recovered within the approved cap. Completed responses were never regenerated; 49 DeepSeek responses that reached the output limit were retained and excluded as malformed.

Ledger cost is **$4.85184842 actual plus $0.09712920 retained uncertain charges**, or **$4.94897762 total exposure**, including preflight. Main collection took about 91 minutes. The ten-minute monitor was removed after collection and offline consensus completed.

## Next step

The [frozen protocol](../../rejudge/phase4_protocol_v1.json) requires a human to check the predetermined 20 changed claims against their full source documents. The private packet is `E:\selvarath-archive\phase4b-blinded-adjudication-2026-09-12\analysis\human_review.md`; the associated JSON records the reviewer's actual decisions and provenance. All 20 entries are currently pending. Disagreements exclude the claim without replacement, after which coverage must be recomputed.

After that review, freeze the approved edit map and prepare the separately budgeted 5,248 fresh original/repaired recipient verdicts. That later comparison will test whether corrections reduce error and whether their benefit differs between Qwen and Llama. This audit's approval does not cover those calls or Phase 4C.

Source commit: `0547a777955b5b64e1ee53a3aae8b9ba1c95662b`. Raw results SHA256: `db3ac531ee0748a21d9f6b88725902a6086d046868662fffcdc94f35910e5e06`. The [aggregate summary](summary.json) records input, analysis and review-packet hashes. Raw claims, source documents and response data remain in the private archive.
