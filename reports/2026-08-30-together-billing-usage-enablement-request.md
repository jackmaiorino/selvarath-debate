# Together billing-usage API enablement request

Date: 2026-08-30. Status: paste-ready support request and read-only recheck procedure. This
document authorizes no provider inference, reviewer dispatch, production execution, support
message, or spend.

## Current verified condition

At local observation time `2026-08-30T21:10:57.7301504Z`, the repository-managed strict capture
command made one authenticated `GET /v1/whoami`, validated its exact response schema, confirmed
the proposed Phase 3 account identity SHA-256, and then made one
`GET /v1/billing/usage?month=2026-08&organization_id=...&granularity=hour&limit=1000`.
Together returned HTTP 404 for the billing request, which the command classified as:

> Together billing usage API is not enabled for this organization

The failed capture published zero files. The command has no redirect, retry, inference-client,
provider-write, or spend surface.

Non-sensitive scope identifiers:

- Account identity SHA-256: `8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e`
- API-key-ID SHA-256: `ef05836c3edeca61b0d88a363fac5656845fff51401c395d206db782c7b378ee`
- Project-ID SHA-256: `47a130970616d501ac049d52e7ae27c9c15ac32cc90e96513b3a934a37e32d7f`
- Organization-ID SHA-256: `7e985a2e00e7e185006561c2d86fccb813cd7d6d8511c0811bc98b08a605fda1`

Do not send the API key. If Together support needs the raw organization, project, or API-key ID,
Jack should supply it only through Together's authenticated private support form or console. Raw
identifiers must not be added to this repository, a public issue, or a chat transcript.

## Paste-ready request

Subject: Enable authenticated hourly billing usage API for Phase 3 reconciliation

Hello Together support,

Please enable the authenticated `GET https://api.together.ai/v1/billing/usage` endpoint for the
organization and project associated with my currently authenticated Together API key. The same
key successfully authenticates `GET /v1/whoami`, but the billing-usage request returns HTTP 404.

I need read-only hourly usage evidence for account reconciliation. The requested query shape is:

`month=YYYY-MM&organization_id=<authenticated organization ID>&granularity=hour&limit=1000`

Please confirm:

1. Whether this organization can be enabled for the billing-usage API.
2. Whether the existing API key and project scope can read the enabled endpoint.
3. Whether hourly reports are finalized through the last completed UTC hour, including the
   expected current-month and prior-month settlement lag.
4. Whether pagination or response-schema requirements differ from the documented `after` cursor
   and hourly report contract.

For correlation, I can provide the raw organization, project, and API-key IDs through this private
support channel. I will not send the API key itself.

Thank you.

## Governed recheck after enablement

Run the tracked command only after Together confirms enablement. Use a fresh, non-existing output
path in the formal evidence root:

```powershell
$phase3BillingOutput = 'E:\selvarath-archive\phase3-main-billing\fresh-capture.json'
.\.venv\Scripts\python.exe scripts/phase3_main_capture_together_billing.py --window-start-utc 2026-08-18T00:00:00Z --window-end-utc 2026-08-30T00:00:00Z --finalized-through-utc 2026-08-29T23:00:00Z --expected-account-identity-sha256 8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e --output $phase3BillingOutput
```

The command must report `inference_calls: 0`, the selected account identity hash, a provider
delta, and a provider-authenticated settlement watermark. A second 404 means enablement is still
absent. Any identity mismatch, redirect, retry, schema drift, secret echo, or incomplete
settlement fails closed and must not be treated as billing evidence.
