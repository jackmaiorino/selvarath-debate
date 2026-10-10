# Independent coordinator audit outcome and W005-Q11 amendment, 2026-10-06

The unpaid independent source audit required by
`reports/final-phase-2026-09-30/design.md:55` is complete. Jack refreshed the
subscription login on October 6. The guarded handoff then resumed the original
coordinating session `cd916c75-3534-4496-8864-d4b4f53f3c36`. It ran on the Max
subscription with extra usage disabled, no API key and tools, hooks and MCP off.
No paid API call was made, and no question was edited, dropped, revalidated or
replenished. The frozen counts stand: 96 candidates, 95 mechanical passes,
72 retained, 23 validation rejections.

## Sampled questions

| Question | Reviewer conclusion | Retained source defect |
|---|---|---|
| W006-Q11 | Key correct, decisively supported | No |
| W006-Q06 | Key correct | No |
| W004-Q04 | Key correct, decisively supported | No |
| W005-Q11 | Revised after world review: key not decisively supported | Yes |

The reviewer found a defect in W005's Economy section. The L. 125 levy receipts
for two guild lines exceed what the stated 1-in-40 levy allows on the stated
landings (Sulve 410 against a 300 maximum; Brann 190 against 97.5), and no rate
change is stated. The table can therefore be read as levy paid by each guild or
as levy collected at each harbour. This triggered the required review of every
retained W005 question.

## W005 world review

| Question | Depends on the guild lines | Conclusion |
|---|---|---|
| W005-Q01 | No (uses the levy rule) | Key correct |
| W005-Q02 | Only the unchartered line | Key correct under both readings |
| W005-Q03, Q05, Q06, Q09 | No | Key correct |
| W005-Q11 | Yes | Not decisively supported |

Coordinator disposition: the defect is confined to the guild lines of the L. 125
receipts table. W005-Q11's key holds only under the guild-payment reading, which
the arithmetic rules out. Under the harbour-collection reading, the Keepers'
Exemption means the table does not measure each guild's payment, so Oske's and
Tarrow's levy paid cannot be determined. The reviewer recommended that W005-Q11
not serve as a confirmatory measured item.

## Packet encoding

The reviewer saw mojibake in the W004 text in the audit packet. An unpaid
byte-level check found it only in the packet serialization: it was stored UTF-8
text decoded as cp1252, and reversing that reproduces the stored text exactly.
All eight stored world files, `bench/validation.jsonl` and `validate.db` are
clean UTF-8 and match their recorded hashes, so validators, debaters and judges
receive correct text. The reviewer withdrew the concern; W004-Q04 is unchanged.

## Prospective amendment: W005-Q11 is sensitivity-only

Jack decided on October 6, before any W005 judge outcome exists, that
W005-Q11 stays in the frozen retained set but is excluded from every primary,
secondary and confirmatory contrast. It is analysed only in a pre-registered
sensitivity analysis that repeats the confirmatory contrasts with W005-Q11
included and reports both results. It does not count toward the main-set
question cap or the 900-question extension threshold in design section 5. If
W005 falls in the pilot or canary split, the same exclusion applies to any
contrast reported there. The full pre-registration frozen before main must
carry this amendment. No other retention rule, seed, split or measurement rule
changes.

## Private evidence

Under `D:/finalphase-runs/final-phase-2026-10-03/`: `coordinator_audit_review.json`
(`audit_complete: true`, `retained_source_defect_worlds: ["W005"]`, the first
response verbatim and the W005 follow-up as `w005_world_review` with its own
provenance), both timestamped `coordinator_audit_max_response_*.json` files, and
the follow-up's `coordinator_audit_followup_w005.ps1`, prompt and input. The
original packet, prompt, script, validation rows and ledger are unchanged.
Resumed-session `total_cost_usd` figures (15.67, then 16.18) are list-price
totals for the whole session history, not new billed inference.
