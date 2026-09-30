# Query gate qualification, 2026-09-30

The gate is the mechanical screen (`rejudge/query_screen.py`) followed by a classifier that sees the question, both candidates (unlabelled) and the query, never the world or key. Qualification bar (design section 6): at least 90% agreement with expected labels.

| Set | Items | Prompt | DeepSeek V4 Flash | DeepSeek V4 Pro |
|---|---:|---|---:|---:|
| Development (world DRY02) | 60 | v1 | 83% (10 errors, 9 false rejects) | 73% (16 errors, all false rejects) |
| Development (world DRY02) | 60 | v2 | 93% (4 false admits, 0 false rejects) | 97% (1 false admit, 1 false reject) |
| Held out (world DRY01), written before any run on it | 40 | v2 | 92% (3 false admits, 0 false rejects) | 90% (2 false admits, 2 false rejects) |

- v1 errors were almost all one confusion: rejecting a decisive atomic fact because a candidate cites it. v2 clarifies that asserting a candidate's conclusion is banned and checking a fact a candidate cites is allowed, and that one rule with its own conditions, one list, or a negative fact each count as one proposition.
- One development item was relabelled before the v2 run: a query with over 70% word overlap with the keyed answer is a restatement under rule 2 of `docs/oracle-query-contract.md` (mechanical screen), so its expected label is REJECT.
- **Decision: DeepSeek V4 Flash** (`deepseek-ai/DeepSeek-V4-Flash-0731`). It passes both sets, never falsely rejected a legitimate query, and costs about a tenth of Pro. Its errors are false admits of borderline evaluative or conclusion-like claims; the pilot audit reviews 100 admitted queries for these.
- Worlds DRY01 and DRY02 are engineering dry-run worlds authored by DeepSeek V4 Pro; they are not part of the benchmark.
- Sets: `finalphase/gate_challenge.json`, `finalphase/gate_heldout.json`. Spend about $0.21 on Together.
