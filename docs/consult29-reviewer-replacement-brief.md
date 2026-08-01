# Consult #29 brief: the canary reviewer is unavailable mid-run

**Status:** DRAFT brief for the owner to put to the external consult. Not a ruling.
**Prepared:** 2026-07-29. **Run state:** canary halted at 554/945 cells, $3.23 of $40 spent.

## The question

The frozen Claude-gate amendment (`rejudge/phase2_canary_claude_gate_amendment_2026-07-23.json`)
pins the blinded second gate to **claude-fable-5** and states: *"Reviewer model/build change
mid-canary is a protocol deviation."* That model's quota is now exhausted account-wide, with
141 of an estimated ~215 unique payloads already ruled by it.

**What should the canary do?**

## What is already fixed and not at issue

Two incidents were recorded and remediated this epoch; neither is the subject of this consult:

1. `incident1` (concurrent writers interleaved the usage ledger) — migrated, spend carried
   forward, archive lock added.
2. `incident2` (six rulings produced from hand-composed prompts containing fabricated
   candidate text) — blast radius zero, store rebuilt under owner approval to contain only
   the 141 rulings whose dispatched prompt bytes were proven byte-exact. The six excluded
   payloads now carry no decision, so their cells will pause for proper re-review.

## The options, as the operator sees them

**(A) Wait for claude-fable-5 quota to restore.** Zero deviation; the gate stays homogeneous
and the estimand is unchanged. Cost: the run is blocked for however long that takes.

**(B) Substitute a different reviewer model for the remainder.** This is the named protocol
deviation. Consequences the operator believes are load-bearing:
  - the reviewer model is bound into the execution manifest, so a substitution requires a
    **new manifest identity** and therefore a **new authorization record**; the current
    authorization names identity `c8ca2438`;
  - the gate becomes **heterogeneous**: ~141 payloads ruled by claude-fable-5 and the
    remainder by the substitute. Gate pass/intercept/ambiguity/clause distributions are
    reported by arm and stage, so a mid-run reviewer change is a confound that would have to
    be disclosed and, ideally, measured;
  - the amendment's estimand language ("results estimate the debate system WITH THIS FROZEN
    CLAUDE GATE") would need amending.

**(C) Restart the canary's review layer under a single substitute model.** Homogeneous gate,
but discards 141 valid rulings and re-spends the reviewer budget; the Together-side spend
(554 completed cells) is unaffected because rulings gate dispatch, not transcripts.

**(D) Re-review the already-ruled payloads with the substitute as a calibration sample.**
Hybrid: quantifies the reviewer-swap effect on the payloads where both models can be
compared, at the cost of extra reviewer calls. Could convert the confound in (B) into a
measured quantity.

## What the operator specifically wants ruled on

1. Is (A) required, or is a substitution defensible mid-canary?
2. If substitution: which of (B)/(C)/(D), and what must be recorded, re-materialized, and
   re-authorized before any further cell executes?
3. If substitution: does the substitute have to be a peer-capability model, and does the
   declared-conflict analysis in the 2026-07-23 amendment (the reviewer authored the
   codebook, knows the worlds, has read Stage-1) transfer or change?
4. Does the answer differ for the **six re-reviews** (payloads a fable reviewer already saw
   under a contaminated prompt) versus the **~74 never-reviewed** payloads?

## Materials the consult should be given

- `rejudge/phase2_canary_claude_gate_amendment_2026-07-23.json` (the binding text)
- `rejudge/phase2_canary_reviewer_transport_2026-07-28.json` + amendment 2 (how the reviewer
  is actually invoked, and why)
- `rejudge/phase2_canary_incident2_2026-07-29.json` (the contamination forensics)
- `rejudge/phase2_canary_store_rebuild_decision_2026-07-29.json` (owner approval, what it
  does and does not authorize)
- `rejudge/phase2_codex_review_2026-07-23_consult27.md` (the prior ruling that substituted
  the assistant for the human reviewer; the closest precedent)

## Operator's own view, offered for the record

(A) is cleanest and (D) is the most scientifically informative if a substitution is needed.
The operator has no authority to choose, and notes that it also has **no tool that can invoke
a GPT-family model**: the subagent transport offers only sonnet/opus/haiku/fable, the API
transport reaches Anthropic models, and the Together roster bound to this experiment is
Qwen2.5-7B, Qwen3.7-Plus, gemma-4-31B, Llama-3.3-70B, and gpt-oss-120b. Any GPT-5.x
involvement must therefore be run by the owner externally, exactly as consults #09, #10, #27
and #28 were.
