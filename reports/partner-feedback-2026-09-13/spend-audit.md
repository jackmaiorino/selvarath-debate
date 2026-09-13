# Project spending reconstruction, 2026-09-13

This is a bounded Together API reconstruction, not a complete project financial statement. No provider calls were made. All source archives were read-only.

The historical provider balance plus later local usage records gives **$1,322.02 recorded usage** and **$148.17 retained uncertain exposure**, for **$1,470.19 conservatively accounted**. Replacing the reconciled August 18-29 local actual with provider-final Cost Analytics gives a **$1,315.17 billing-adjusted usage estimate**, before subsequent unknown charges are settled. Neither number is an all-provider invoice total.

The declared spendable budget is $8,000, within a $10,000 headline grant. After the conservative Together subtotal, the conditional remainder is **$6,529.81 before unreconciled reviewer/subscription or other expenses**. Current Together prepaid credit is unverified. The recorded $1,800 top-ups were funding transfers, not extra usage.

| Component | Recorded usage USD | Retained uncertainty USD |
|---|---:|---:|
| Together baseline through July 19 | 210.51000000 | 0.02215803 |
| phase2_checker_selection | 0.37011862 | 0.26390377 |
| phase2_canary | 6.58120611 | 1.59745802 |
| phase2_bridge | 5.40505548 | 2.01316856 |
| phase2_main | 164.50345346 | 8.35612392 |
| phase3_void1 | 24.89969640 | 0.00000000 |
| phase3_void2 | 36.07555192 | 0.08092240 |
| phase3_main | 536.90824712 | 111.40412616 |
| phase3_probes | 0.19559800 | 0.00000000 |
| Phase 3 canaries, 18 selected ledgers | 95.68306918 | 23.58931572 |
| Phase 3 auxiliary screens | 5.23608743 | 0.23533403 |
| phase4a-history-crossover-2026-09-12 | 51.99527168 | 0.00000000 |
| phase4b-blinded-adjudication-2026-09-12 | 4.85184842 | 0.09712920 |
| phase4b-label-repair-recipients-2026-09-12 | 71.08261336 | 0.35333200 |
| phase5-evidence-scope-2026-09-12 | 107.71741704 | 0.15679872 |

Accounting method:

- Used the provider-confirmed $210.51 baseline at July 19, then added only later project ledgers. Earlier calibration, packaging, Gemma recovery and capability preflight are already included.
- Parsed success, unknown_charge and reserved rows by physical attempt_id. A terminal success or unknown-charge replaces its reservation. Unique physical retries still count. Copied events and retired-ledger copies do not count twice.
- Included Phase 2 retired main ledgers and the first canary ledger. This reproduces the reported $172.85957738 Phase 2 main exposure, of which $164.50345346 is recorded success cost.
- Included both voided Phase 3 main runs. Their combined $61.05617072 exposure appears as carry-forward in the finalization and is counted only once.
- Verified all 18 prior Phase 3 ledger SHA-256 values against the saved reconciliation. Both auxiliary screen files also match their inventory hashes. Included their $5.23608743 actual plus $0.23533403 uncertainty separately from selected canaries.
- Phase 4 and 5 figures come from final status spend records and already include preflight; do not add the preflight fields again.
- The $94.07 provider settlement covers August 18-29. It is an alternative to the local amount for that window, not another charge. The August invoice amount due was zero because usage drew prepaid funds; zero due is not zero usage.
- No simulations, completed archive snapshots, forecasts, spending caps or prepaid deposits were added as usage.

Unresolved gaps:

- Not a current provider invoice or account balance. Historical billing evidence and local recorded usage are combined.
- Baseline is provider-confirmed Together project usage 210.51 USD through 2026-07-19T23:20:00Z. Earlier individual stage amounts are included, not added again.
- Anthropic reviewer charges, ChatGPT/Codex/Claude subscriptions and overages, and any other project expenses are not reconciled here. Phase2 approval explicitly excludes Anthropic reviewer billing from Together ledgers.
- Historical August 18-29 provider Cost Analytics settles 94.07 USD versus 100.91915661 USD local actual including auxiliary screens; preserve a billing-adjusted estimate separately from conservative local accounting.
- A 0.00006885 USD Qwen3.5 screening estimate is recorded outside the chosen measurement ledgers. It is below one cent and omitted from exact locally reconstructed actual totals. It falls inside the reconciled August window.
- No current provider balance, later credit top-up record, or all-provider statement was obtained. The conditional remainder from the 8000 USD spendable budget assumes no additional unlisted project expenses.
- Retained uncertainty is a conservative reservation, not proof the provider charged it. Historical unresolved reservations were retained at their last reserved cost; no live work is implied.
- Test and harness simulations, archive snapshots, copied ledgers, prepaid credit purchases, caps, and forecasts are excluded as additional spending.

Key sources:

- [Machine-readable reconstruction and exact ledger paths](E:/selvarath-archive/partner-feedback-2026-09-13/spend_audit.json)
- `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase2_provider_reconciliation_2026-07-20.json`
- `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase3_main_together_console_billing_evidence_2026-09-04.json`
- `E:/selvarath-archive/phase3-main-7172776-2026-09-07/main_finalization.json`
- `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase2_canary_spend_confirmation_2026-07-28.json` explicitly records Anthropic reviewer billing outside the Together ledger.
- `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase3_main_reviewer_usage_policy_2026-08-30.json` also keeps reviewer service accounting separate and makes no zero-cost claim.
