# Update: capability preflight complete; checker ground truth sealed (DRAFT, not posted)

Status draft for Jack's review. Facts current as of 2026-07-21. Do not post until approved.

## Capability preflight: done, clean, $1.27

The pre-registered capability preflight (5 models x 212 solo questions, 1,060 cells) completed on 2026-07-19 with zero errors, zero halts, and spend of $1.27 against the $15 approved cap. Local cost accounting matches the provider dashboard to the cent. Project spend to date: about $211 of the $10k grant.

One launch attempt before it aborted at call zero with zero spend: an SDK argument-binding incompatibility tripped the fail-closed halt exactly as designed. We recorded a closure artifact, added installed-SDK signature gates, and relaunched under a fresh sealed manifest. The abort cost nothing and changed no science.

## Preflight results and one honest wrinkle

Under the frozen strict-parsing rule, Qwen2.5-7B scored 206/212 and is the mechanically selected anchor. Llama-3.3-70B scored 153 with 57 invalid answers, every one a trailing-period formatting artifact; under period-tolerant parsing it scores 209/212 and would win instead. We executed the frozen rule as written, changed nothing mid-run, and escalated the parsing-sensitivity question to our external review oracle (available 7/24) before the second anchor is bound. Both readings are published in the repo.

## Checker validation ground truth: 350 items labeled

The runtime query checker (the guard that keeps debaters' oracle queries atomic and non-leading) needs a labeled validation set before we select a checker model. Labeling was originally split between two human labelers; after progress stalled, both approved adopting the assistant's sealed labels, which had been produced blinded (no item metadata, committed before any human viewed an item). The full audit trail is in append-only amendments in the repo, including the change of plan.

Result: 350 items, 211 allow / 139 reject / 0 unresolved. The zero is itself a finding: the frozen contract decides every real query, which leaves one pre-registered gate (recall on unresolved items) unmeasurable. We did not manufacture doubtful labels to fill the quota; the disposition goes to external oracle review on 7/24. The selection run itself is cheap (under $1) and stays blocked until that review and an explicit spend approval.

## Next

Oracle review of the amendment chain and open questions (7/24), checker-model selection, then the canary run and, on its gate, the main grid (estimated $550 to $1,200, ceiling $1,500). All spend remains gated on explicit line-item approval.
