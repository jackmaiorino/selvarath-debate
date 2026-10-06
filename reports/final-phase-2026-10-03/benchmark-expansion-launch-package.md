# Prospective benchmark expansion package, 2026-10-05 Eastern

Status: preparation complete; independent Claude audit pending; paid launch is
not authorized. Canonical delivery is PR #1 on `codex/final-phase-next-stage`.
Claude's `claude/final-phase` worktree remains separate. Original evidence is
under `D:/finalphase-runs/final-phase-2026-10-03`; existing manifests and ledgers
are reused, with a WAL-safe preparation snapshot under
`preserved/pre-expansion-preparation`. Original eight-world decisions remain
96 candidates, 95 mechanical passes, 72 retained and 23 validation rejections.

## Independent audit and exact handoff

The required reviewer is the existing Claude coordinating session
`cd916c75-3534-4496-8864-d4b4f53f3c36`, located in the saved Claude project
`C:/Users/Jack/.claude/projects/C--Users-Jack-Dev-FailureModeExperiment`.
It is the final-phase session originally attached to Claude's worktree.
No active local CLI process for that session was found. Authentication with API
key variables removed identifies a Claude Max subscription. Extra-usage billing
status was not verified. A tool-disabled resume with `--max-budget-usd 0` failed
locally: `--max-budget-usd must be a positive number greater than 0`.
No model call or positive-budget retry was made. This does not establish that
the reviewer refused or that its subscription is exhausted.

The handoff is [coordinator-audit-handoff.md](coordinator-audit-handoff.md).
The original packet, full source input and the implementer's per-question
evidence are private `validation_audit_packet.json`, `coordinator_audit_input.json`
and `implementer_source_review.json`. The implementer found no material retained
source defect in the four questions. That is preparation, not Claude's audit.

| Question | Implementer source conclusion | Deciding source evidence |
|---|---|---|
| W004-Q04 | Key supported; all four facts grounded | Livestock relief excluded; six votes cannot waive restrictions; transferred money loses former discretion; proposed grant is livestock relief |
| W005-Q11 | Key supported; all three facts grounded | Oske/Tarrow changes from 1/2 lamp votes to 90/60 levy receipts; the question asks about the proposed vote apportionment |
| W006-Q06 | Key supported; all four facts grounded | Full free grain entitlement, funded stocks and protection against debt seizure meet the stated seasonal borrowing goal |
| W006-Q11 | Key supported; all four facts grounded | Spawning moved to Orven; closure and clean inspection cover Kelmur; netting continued through Orven spawning |

Question criteria and rubrics align. W005-Q11 has one recorded reversing-reading
flag about broader political influence; this is not an equal-defensibility
filter or an implementer reason to drop it. W005's opening geographic list omits
Oske's name, but its lamp/vote/receipt facts relevant to Q11 are consistent.
Claude must assess these independently. A material retained source defect requires
reviews of **every retained question from that world** and a coordinator
disposition. Nothing is edited, replenished or removed by this package.

No watcher or recurring task was created. The next wake condition is an actual
review returned by that session, or a user decision supplying an available
unpaid route to it. The handoff is ready for the original session.

## Truncation diagnosis from all saved raw responses

All 29 are DeepSeek Pro blind key checks, with `finish_reason=length` and exactly
4,000 output tokens. Twenty-seven have empty final content. W003-Q05/key_a has
206 final characters after 3,949 reasoning tokens; W007-Q08/key_a has 675 after
3,821 reasoning tokens. W003-Q07/key_a reports 3,999 reasoning tokens with no
final content; the other 26 report 4,000 reasoning tokens. Partial final text
and reasoning are not salvaged for retention.

| Dimension | Truncated / DeepSeek key-check exposure |
|---|---:|
| Fable author | 26/94 (27.66%) |
| Astra author | 3/96 (3.13%) |
| Key in candidate A | 14/95 (14.74%) |
| Key in candidate B | 15/95 (15.79%) |
| interest_alignment | 10/32 (31.25%) |
| relative_position | 8/30 (26.67%) |
| policy_outcome | 4/30 (13.33%) |
| causal_assessment | 3/32 (9.38%) |
| counterfactual | 3/32 (9.38%) |
| institutional_capacity | 1/34 (2.94%) |

The pattern is output-budget exhaustion, with a descriptive association with
author and task type, and little answer-order imbalance. This cohort cannot
identify a causal author-quality effect. Seven questions truncate in both
orders; fifteen in one order, affecting 22 frozen rejections. There are no
truncated fact checks or frontier key checks, no parsing failures among completed
OK responses, no completed key disagreement and no equal-defensibility rejection.
The distinct substantive rejection is `factcheck:W003-Q08:2 = NO`.
Two original 503 transport failures were separately resolved under prior approval;
their saved errors and $0.04939308 possible-billing reserve remain. No unresolved
transport response remains. The frozen `no_response` label also covers measured
truncations, and is not evidence of transport loss.

`validation_truncation_diagnosis.json` contains every truncated identity,
raw-response hash, input/output/reasoning usage, order, author, task and class.
Raw provider bodies remain in `validate.db`; no original result changes.

## Validation-token-v2 proposal, not applied

Only future DeepSeek Pro **key-check** output allowance would rise from 4,000 to
12,000 tokens. Frontier key checks stay at 4,000 and facts at 2,000. Models,
reasoning settings, prompts, blind keys, fresh contexts, both answer orders,
parsers and retention rules remain fixed. Production still uses v1. New paid
identities must carry a distinct protocol version; old IDs cannot change bodies.

The qualification uses 12 **retained** questions, one for each author/task cell,
selected by the versioned hash in the saved proposal. Both key orders are run
serially and with eight workers: 48 scratch Together calls. No rejected question
is retried and no control contributes to retention. Maximum: **$2.91965520**.

Acceptance: all 48 calls complete with the exact model ID, no truncation/refusal,
valid answer and both flag fields, and unchanged request content except fresh
scratch identity and output allowance. Record all usage, raw bodies, disagreement
and order differences. Matching the saved key or improving retention is not an
acceptance criterion. Measure completed calls including collection and storage;
select the faster valid serial/parallel allocation. On failure, stop; no automatic
retry, higher cap, effort change or prompt tuning is allowed.

Zero truncations in 48 controls does not demonstrate a population rate below 1%
(one-sided 95% upper bound is about 6.1%), and retained controls do not reproduce
the rejected difficult tail. Future v2 truncations remain failures, with a cohort
stop for protocol review, rather than selective revalidation.

Protocol consistency needs an explicit decision: retain v1 uniformly for all
future worlds, or approve v2 uniformly for every future question in a separate
versioned benchmark. Under v2, preserve the original eight as a v1 engineering
cohort and exclude them from pooled primary v2 claims. Do not combine v1/v2
retention decisions as exchangeable. The consequential effect on the 160-world
split, canary/pilot counts and minimum-main rule must be resolved prospectively
with the coordinator before expansion. No split or protocol is changed here.

## Receipt forecast versus dispatch reservations

All costs below use frozen rates and exact decimal arithmetic. Eight-world
validation spend is $8.76297396 plus the possible original-503 reserve. Existing
authoring/access/validation bound is **$30.89985840**. Prepaid credit and campaign
authority are separate from provider monthly/project capacity.

| Remaining 152 worlds | Receipt extrapolation | Conservative dispatch bound |
|---|---:|---:|
| Authoring, no new failed generation | $132.484435 | Not a dispatch reservation |
| Authoring, observed correction retry burden | $193.700630 | $374.552280 for two generations/world |
| Validation, current receipts | $165.73886352 | $789.67464308 for 19 copies of saved v1 bodies |
| Validation, v2 tail stress | $183.19454352 | $904.03944308 for 19 copies of saved v2 bodies |
| Future v2 validation, all 12 questions and five facts | Unknown final tokens | $963.03066816 under the declared byte envelope |
| One possible exact-body transport retry per future validation request | Not assumed spent | $963.03066816 |
| Eight extra serial validation qualification controls | Not assumed spent | $0.32760816 |
| Scratch author qualification, four calls | Not measured | $4.928130 |
| Scratch token qualification, 48 calls | Not measured | $2.91965520 |

Recent Fable correction used six paid generations for four admitted worlds;
Astra used four for four. These forecasts extrapolate small observed samples.
The 19-copy figures are not full future reservations because future texts and
mechanical-pass counts do not exist. The hard envelope permits 76 worlds per
author, all twelve candidates and up to five facts each, with the maximum saved
UTF-8 request bound for each author/role. Every future request must fit that
envelope before registration; otherwise stop and obtain a revised reservation.
No shorter prompts or cached-input discounts are assumed. Author retries are two
generation attempts; validation transport retries require separate approval and
distinct journaled identities. Truncation and substantive disagreement are final.

Full incremental preparation reservation: **$2,308.78900968**.
Cumulative preparation bound: **$2,339.68886808**. This includes qualification,
both author generation slots, full validation, one validation transport retry
slot each, eight later serial controls and existing uncertain billing.

| Provider | Incremental maximum | Saved credit estimate | Planning deposit gap |
|---|---:|---:|---:|
| Anthropic | $808.405100 | $17.467410 | $790.937690 |
| OpenAI | $679.009390 | $796.719905 | $0 |
| Together | $821.37451968 | $851.49512296 | $0 |

These are estimates from prior user reports and this campaign's ledger, not live
balances or invoice reconciliation. Unrelated usage remains unknown. The small
52-call qualification needs no deposit against those estimates, but needs current
confirmation before dispatch. Do not deposit the whole campaign in advance.
Confirm purchase increments only when a funded next stage actually needs credit.

Known October OpenAI usage is $5.032295. The saved $120 setting has estimated
$114.967705 headroom. Full preparation needs another $679.009390, so the known
minimum total monthly capacity would be **$684.041685**, excluding other account
usage. Confirm OpenAI-approved organization allowance, organization hard limit,
project hard limit or explicit disabled status, and queued-token capacity for the
actual project. The inference key's prior management/billing requests were denied;
no further account setting or purchase is performed. Prepaid credit does not
establish these capacities. See [OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits)
and [rate limits](https://developers.openai.com/api/docs/guides/rate-limits).

Existing stage-cap conflicts are exact: author needs $401.566975 including
settled authoring and author qualification, exceeding $200 by $201.566975;
validation needs $1,938.12096672 including current settlement/reserve and token
qualification, exceeding $200 by $1,738.12096672. Even the observed author retry
forecast plus settled authoring and author qualification is $220.715325.

One allocation proposal rounds the preparation needs up to $402 author and
$1,939 validation. Keeping $60 canary, $650 pilot and $590 reserve leaves $2,359
for main. Those later caps still conflict with the old high forecasts. A second
proposal uses $131 canary and $1,365 pilot including oracle selection, leaving
**$1,573 main**, with the same $590 reserve. Both sum to the unchanged $6,000.
These are proposals, not cap edits or qualified later-stage reservations. Main
at 1,068 questions already forecasts $4,186.61 to $7,470.53. A smaller count can
only be selected prospectively on cost after a measured pilot; all eight judges,
strong Fable/Astra debaters and the 0/1/2/6 curve are preserved in this package.
The potential 40-world extension has no launch approval and would require its
own count-driven reserve within the ceiling. No adequacy or precision claim is
made for a reduced sample.

## Concrete next approval: qualification only

Requested next scope is **52 scratch calls**, maximum **$7.84778520**:
Anthropic $3.267580, OpenAI $1.660550 and Together $2.91965520. Four author controls
compare one outstanding frontier batch request at a time with both providers in
flight, using matched frozen prompt/seed inputs, complete collection and world
admission. These measure remote batch concurrency, not local CPU acceleration.
Controls are not promoted to the benchmark. The 48 token controls are above.
Author controls must all pass the existing 1,000..1,500 word/schema admission.
No failed control is retried. Scratch requests and approval bind exact hashes.

The qualifier stops after its receipt. Author/validation protocol application,
cap reallocation, additional authoring and all later stages need separate explicit
approval. Current `expansion_qualification_approval.json` is an **unapproved**
template with capacity fields unresolved. Execution refuses it before Store
creation or provider networking. It also refuses an incomplete coordinator audit,
changed source/worlds/results, missing capacity, STOP files or missing placement
inventory. Durable measured identities are reused; ambiguous acceptance cannot
be resent and cached replay cannot masquerade as fresh throughput.

Unpaid preparation and plan commands, from the owned workspace:

```powershell
uv run --locked python scripts/finalphase_expansion_prepare.py
uv run --locked python scripts/finalphase_expansion_qualify.py plan
uv run --locked python -m finalphase.cli preflight --stage author --workers 8 --mode batch
```

Only after the actual unpaid audit, separately recorded paid approval and current
capacity/placement confirmation:

```powershell
uv run --locked python scripts/finalphase_expansion_qualify.py execute --approval D:/finalphase-runs/final-phase-2026-10-03/expansion_qualification_approval.json
```

Recheck Jack's PC, HaleysPC and authenticated RunPod inventory without changing
trust or reservations. The prior snapshot selected Jack as the local collector,
HaleysPC failed strict host-key verification and all five RunPod pods were exited.
Remote provider inference is the bottleneck; do not rent a local GPU on that
snapshot. Qualification records actual serial/parallel completion, collection,
SQLite storage and startup overhead. Preserve input hashes and choose the fastest
valid allocation. The original validation speedup of 2.620208x remains historical
evidence; it does not qualify authoring or the new token protocol. Qualification
controls cannot modify the frozen original stores or decisions.

Conditional later supported commands remain `author --worlds 160 --mode batch`
and `validate --workers 8 --mode batch`. They are **not ready to execute**: current
author scope/funding/audit guards and validation world hashes, source qualification,
envelope and $200 caps refuse expansion. After approval, implement the selected
version/caps and register compatible workload receipts and exact future world
hashes before those commands. No raw Store launcher is a substitute. Canary,
pilot and main likewise require their own compatible qualifications and scientific
gates; none are part of this next approval.

Remaining decisions: provide the unpaid original-session audit; approve or reject
the 52-call scratch qualification; choose uniform v1 or separately analysed future
v2 and reconcile cohort/split implications; approve a feasible allocation within
$6,000; reconcile actual balances and OpenAI organization/project capacity.

## Verification and delivery

Affected checks, zero-provider replay and final delivery receipts are recorded in
the existing manifest and the opening readiness report. Author and validation
primary hashes remain `716b1bebde52d66f1e1bbdc396dbd6b2bfbd99e905a87967cc64a40185216a38`
and `a5308a975aca365b4ddc10e1979597aa7d13c784c03b22933d59956f43bbf38b`.
The 96 frozen validation rows remain
`f9da114c47e3d0af6b80bfaa2cb512ef5bbca5ce1c61b9a21a8f598aaf63f77a`.
WAL-safe snapshots compare all calls, batches and events, not just main DB files.
Historical full CI failed at the previous head; passing affected checks do not
clear that failure or constitute independent scientific approval. PR #1 remains
the canonical draft with its existing base; no merge or default-branch verification
is claimed. The public repository receives source and summary reports only;
worlds, raw responses, detailed quotations and private approval records stay out
of Git.
