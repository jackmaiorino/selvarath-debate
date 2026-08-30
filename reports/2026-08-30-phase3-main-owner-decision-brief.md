# Phase 3 main owner decision brief

Date: 2026-08-30. Policy source commit: `fe66fa92e39eb99f26b2f0493023da3d9a5e4e43`.
Status: decision support only. This brief authorizes no reviewer dispatch, provider call,
production run, or spend.

## Decisions that can be made now

| Decision | Lead recommendation | Exact bound evidence |
|---|---|---|
| Provider price-change response | Ratify the candidate unchanged. A detected change stops new logical Together calls, permits already-started work to finish and be accounted, prohibits automatic reprice or resume, and requires a fresh snapshot, forecast, cap, manifest, and authorization. | `rejudge/phase3_main_price_change_policy_2026-08-30.json`, raw SHA-256 `88714908541e9957d6150186adad4a23c87876de7c7cf5654c9c585e8f14cd8d` |
| Reviewer usage treatment | Ratify the candidate unchanged. Cap main-run usage at 59,040 external reviewer dispatches. Count reservations before release, including failed or ambiguous dispatches. Keep dispatch count separate from Together USD accounting and make no zero-cost claim. | `rejudge/phase3_main_reviewer_usage_policy_2026-08-30.json`, raw SHA-256 `8e22e89ce2a816aca5008160f846e0085661813d853ae4e9e380cbcf260f5251` |
| Billing measurement ledgers | Include all 17 exact measurement-ledger source IDs, conditional on Jack confirming that these are the complete Phase 3 measurement ledgers for the half-open window. | Source-selection proposal raw SHA-256 `ac4769f39d1a7a9418cd9e0720625413205af090fb27b05dc41a2355cea2f1cc` |
| Billing journal-validation ledger | Include the separate `phase3-v3-journal-validation` ledger. It has its own ledger identity and no repeated attempt ID in the local inventory. | Same source-selection proposal |
| Billing auxiliary screens | Exclude the two auxiliary screens from the authoritative ledger union, but retain them as explanatory side evidence. They have no durable cross-format call identity, so including them would require an owner assertion of disjointness that the artifacts cannot prove. | Same source-selection proposal |
| Owner signing key | Select the only discovered public-key candidate, if it is the key Jack intends to control for Phase 3 signing: `C:/Users/Jack/.ssh/id_ed25519.pub`, fingerprint `SHA256:e3z7s2CQDDLg2nx/XDI93Tm+JerqZSo8H0GRL88Szmk`. The private key must remain outside the repository and inaccessible to Codex. Capacity and main authorization now read one still-unpinned configuration in `rejudge/phase3_owner_signing.py`, so ratification requires one reviewed source edit. | Local public-key fingerprint check on 2026-08-30 |
| Together account scope | Select the currently authenticated key scope if it is the intended Phase 3 account: account identity SHA-256 `8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e`. | Read-only `/v1/whoami` returned 200 at provider time `Sun, 30 Aug 2026 20:03:33 GMT`; API-key-ID SHA-256 `ef05836c3edeca61b0d88a363fac5656845fff51401c395d206db782c7b378ee`, project-ID SHA-256 `47a130970616d501ac049d52e7ae27c9c15ac32cc90e96513b3a934a37e32d7f`, organization-ID SHA-256 `7e985a2e00e7e185006561c2d86fccb813cd7d6d8511c0811bc98b08a605fda1` |

The recommended billing selection produces a local ledger envelope of actual
`$95.68306917999999801859`, uncertain `$23.5893157200000000378`, and accounted upper bound
`$119.27238489999999805639`. The two excluded auxiliary screens remain visible and explain up
to `$5.4714214599999999661` of provider-side activity, but they do not enter the authoritative
ledger union. Provider-authenticated billing evidence must still land inside the selected
ledger envelope before reconciliation can close.

## Exact non-execution ratification text

If the recommendations are correct, Jack can reply with the following text:

> I ratify the two exact runtime policy files and hashes in the 2026-08-30 Phase 3 main owner
> decision brief, including the 59,040-dispatch reviewer ceiling and separate non-USD usage
> treatment. For predecessor billing, include all 17 measurement ledgers and the separate
> journal-validation ledger, exclude the two auxiliary screens from the authoritative ledger
> union while retaining them as explanatory evidence, and treat that selection as complete for
> the bound 2026-08-18T00:00:00Z through 2026-08-30T00:00:00Z half-open window. I select the
> public signing key fingerprint SHA256:e3z7s2CQDDLg2nx/XDI93Tm+JerqZSo8H0GRL88Szmk. I also
> select Together account identity SHA-256
> 8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e as the intended Phase 3
> account scope. This ratification grants no reviewer dispatch, provider call, main run, or spend
> authority.

Any amendment should name the exact row or hash being changed. A general instruction to continue
does not count as this ratification.

## Decisions that must wait

1. Together must enable `/v1/billing/usage` for the selected organization. The current read-only
   recheck returned 404 at provider time `Sun, 30 Aug 2026 20:03:33 GMT`, while `/v1/whoami`
   returned 200. A second strict recheck at local observation time
   `2026-08-30T21:10:57.7301504Z` again confirmed the selected account hash before returning the
   specific not-enabled error, and published zero files. The paste-ready support request is
   `reports/2026-08-30-together-billing-usage-enablement-request.md`. After enablement, a fresh
   authenticated account identity, complete billing capture, and settlement watermark must be
   materialized.
2. The 180-dispatch reviewer-capacity preflight requires a fresh execution manifest and a separate
   short-lived signed authorization. After the exact manifest exists, the capacity CLI can write
   a reviewable non-authorizing draft with `--write-unsigned-authorization`; it cannot sign,
   validate as authority, dispatch, or execute. The frozen plan raw SHA-256 is
   `11989a20c09f46093759ce0c7a5bf643b8e52d0a9b262b78a4a1bf6459bb855f`. It grants no Together
   or main-run authority. The launch-input handoff, crash accounting, fixed production wiring,
   and v6 main provenance checks are implemented and verified. Execution still requires Jack to
   ratify the signing key, then separately sign the exact 180-dispatch capacity authorization.
   The offline cohort-1 workload is materialized at
   `E:/selvarath-archive/phase3-main-review-capacity-preflight-2026-08-29/cohort_01_workload`
   with exactly 180 packet files and an empty dispatch history. A non-authorizing readiness
   manifest for source commit `786870649d86c11b7714a21ace747ea38ea3f6e5` has raw SHA-256
   `4b973af20b7d89a1d32d6b0aeda49110904a9ceb8445567984b79a3593d57a83` and canonical SHA-256
   `c2077ced6a937c87be007e06f1a8c2401021231777bf082544554c8ac3c08807`. It is only a validated
   readiness specimen. Pinning the selected owner key changes the source commit, so the exact
   execution manifest must be rebuilt afterward before any authorization is signed.
3. The main stage cap cannot be ratified from the current `$875` proposal. It must follow the
   authenticated billing reconciliation, fresh prices, capacity result, and fresh certified
   forecast. The frozen protocol's `$450` value and the `$875` proposal remain unresolved until
   that calculation exists.
4. The final main authorization must be generated from the exact clean source commit, regenerated
   harness receipt, output roots, account identity, price snapshot, reconciliation, forecast,
   capacity evidence, policy hashes, and chosen cap. It is a separate signed authority and the
   only artifact that may authorize provider calls or main-run spend.
