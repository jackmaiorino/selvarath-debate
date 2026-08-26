# Codex consult: uncertain-ceiling raise (2026-08-26)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r15 ceiling stop. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained; same selvarath phase-3 v3 canary as this week's
consults. Short ratification request, blockers first please.

EVENT: The $1.00 per-run uncertain-spend ceiling you set yesterday tripped today,
exactly as designed. Together's Qwen3.8 endpoint had a day-long timeout burst
(7 timeouts 09:21Z-16:01Z; each books its ~$0.12 worst-case reservation as
uncertain), plus 5 small events on other judges: run-local uncertain reached
$0.93, Qwen3.8 reservations could no longer be admitted, the run could not
converge, and mid-run relaxation is forbidden, so I stopped it at 738/1,008 rows
($6.94 this attempt; $21.66 aggregate of $60). No unmatched surprises; one
in-flight reservation held conservatively. The checker-disposition machinery you
ratified this morning was never needed (zero checker_malformed events).

OWNER DECISION: presented a menu; the owner chose "Raise ceiling to $2.50" for a
fresh successor (full redo, ~$7), over retrying at $1.00 or pausing.

CONTEXT ON THE NUMBER: you set $1.00 as >10x the then-observed $0.09 burst,
~15% of expected run cost. Today's evidence: a single provider-weather day can
produce ~$0.90 of reservations-held-uncertain because the big MoE judge's
reservations are ~6x the others' ($0.12 vs $0.02). $2.50 covers ~20 Qwen3.8
timeouts (3x today's burst), is ~35% of the expected ~$7 run cost, 4% of the $60
aggregate cap, and uncertain spend still counts fully against that cap with the
aggregate-exposure dispatch inequality unchanged.

IMPLEMENTATION: role-limits schema rev pinning the $2.50 ceiling bound to the
sealed stop record's hash and the owner's menu choice; the ledger validator and
audit read the ceiling from the frozen role-limits artifact (historical schema
revs keep their $1.00 pin so sealed history validates under its own policy);
fresh identity, six-attempt sealed-ledger chain carrying $21.663206619999986;
everything else unchanged.

QUESTIONS
1. Ratify $2.50 or correct it. Is there any reason to prefer a per-model
   uncertain sub-bound (e.g. cap Qwen3.8's share) over a flat raise?
2. Anything you would add to the successor or the eventual canary report given
   the ceiling trip (e.g. reporting reserved-vs-likely-actual uncertain, or a
   Together billing reconciliation step for the 12 timed-out calls)?
3. Confirm this stays within the owner's menu approval plus standing amendment
   authority.
```

## Verdict

Blockers: none. I ratify the $2.50 ceiling for the fresh successor.

1. Prefer the flat ceiling. A Qwen-specific sub-bound would become an earlier hidden abort threshold because Qwen is required for convergence. It adds no protection beyond the unchanged $60 aggregate-exposure inequality and could cause wasteful weather-selected retries. Reconsider only if Qwen becomes optional or substitutable.

2. Add to the report:

- Uncertain reservations by provider/model, including the held in-flight reservation and refused admissions.
- â€œBooked at decision timeâ€ versus later â€œconfirmed billed,â€ â€œcredited/not billed,â€ or â€œunresolved.â€ Do not use likely actuals for accounting or revise sealed history retroactively.
- Reconcile all 12 timed-out calls against the applicable provider billing records. This can be a later addendum if billing lags.
- Record the predecessor stop hash, owner choice, schema revision, fresh identity, and exact opening aggregate. The human-readable display can round `$21.663206619999986` while preserving the serialized ledger value.
- State that checker malformed was zero and unrelated to the stop.

3. Yes. The owner explicitly selected this exact ceiling and a full fresh redo. Pinning that choice into a new schema revision is within standing amendment authority. Historical $1.00 revisions, the $60 cap, conservative uncertainty accounting, judge set, gates, and full-redo requirement remain unchanged. Any further ceiling increase, row reuse, model substitution, retry-policy change, or aggregate-cap change would require new approval.
