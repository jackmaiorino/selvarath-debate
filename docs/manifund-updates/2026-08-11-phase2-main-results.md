# Phase-2 main run complete: the pre-registered result

The design we froze and posted on 2026-07-18 has now run to completion: 22,140 cells, 100% completion, analysis pinned before unblinding and run once. Total run cost $172.86 against a $173.80 projection.

**Topline: in our three fictional worlds, giving judges a two-call oracle budget in sequential debate increased their error by 3.9 percentage points (CI [2.2, 5.7], Holm p = 0.0006).** The harm finding from the corrected pilot re-analysis (+3.4pp) is consistent with this fully pre-registered estimate. The effect is robust to the pre-declared valid-only sensitivity (+4.6pp).

The pre-registered decomposition attributes +2.6pp to oracle content (robust) and +1.4pp to interactive packaging (statistically detectable but fragile across estimands). Two secondaries both cleared their Holm family at p = 0.0004: removing debater word caps cost the Llama-70B judge 16.5pp more against Qwen3.7-Plus self-play than against Llama self-play (capped error 1 to 2% against both; uncapped 2.9% vs 20.3%), and debate underperformed matched evidence-only presentation by 9.7pp, a disadvantage that persisted with verification rather than being created by it.

Exploratory: harm point estimates are positive for the two weaker judges and near zero for the two stronger ones. Capability dependence is the natural phase-3 confirmatory question; we make no confirmatory capability claim now.

Process disclosure: a mid-run reviewer outage plus a caching bug fabricated 329 oracle-gate rulings. We measured the contamination (1,070 cells), predeclared the remediation before seeing any replacement ruling, re-ran every affected cell under corrected rulings, and verified the converged store references no fabricated ruling. The full audit trail (14 governance amendments, 5 incident records, hash-chained stores) is in the repository.

Full report: reports/2026-08-11-phase2-main-results.md. Results artifact with integrity hashes: analysis_out/phase2_main_results.json.
