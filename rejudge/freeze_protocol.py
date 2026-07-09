"""Render the frozen pre-run protocol (pre-registration artifact) from live config."""
from __future__ import annotations

from pathlib import Path

from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, JUDGE_MODEL, PLACEBO_TEXT
from rejudge.parsers import PARSER_VERSION
from rejudge.records import get_git_sha


def render() -> str:
    arm_rows = "\n".join(
        f"| {a.name} | {a.oracle_normalizer} | {a.composer} | {a.done_detector} | "
        f"{'yes' if a.placebo else 'no'} | {DEFAULT_BUDGETS[a.name]} | "
        f"{'per-budget (pilot replay)' if a.randomize_ab_per_budget else 'fixed'} |"
        for a in ARMS.values())
    return f"""# Re-Judge Protocol (frozen pre-run)

**Frozen at commit:** `{get_git_sha()}` *(state of the harness config when this document was
generated; the document itself lands in the following commit)* · **parser_version:**
`{PARSER_VERSION}` · **Replicates:** K={DEFAULT_REPLICATES} (legacy K=1)
**Judge = Oracle model:** `{JUDGE_MODEL}` · **Transcripts:** the 318 pilot transcripts (`data/transcripts.jsonl`), unchanged.

## Arms

| arm | oracle normalizer | composer | DONE | placebo | budgets | A/B assignment |
|---|---|---|---|---|---|---|
{arm_rows}

Placebo feedback text (verbatim):

> {PLACEBO_TEXT}

## Why each arm exists (design rationale)

- **clean** — the pilot as *intended*: bare-claim queries wrapped exactly once, oracle replies
  preserved (including NOT ADDRESSED; INVALID never coerced), robust DONE detection, strict verdict
  parsing. Carries the primary gate: does a small verification budget hurt when the pipeline works?
  Budget 5 re-measures the recovery side of the pilot's U-shape.
- **both** — the pilot as it *actually ran*: the two oracle-channel bugs faithfully re-injected
  (queries double-wrapped into "Is it supported by the text that Is it stated in the text that X?";
  every oracle NOT ADDRESSED delivered to the judge as an authoritative NO). Same transcripts and
  fixed A/B as clean, so BOTH−CLEAN is a paired estimate of exactly what the bugs cost.
- **placebo** — deliberation control. Budget-0 judges go straight to verdict; budget>0 judges get
  extra conversation turns first, regardless of oracle content. The placebo keeps the full
  query/turn structure but replaces each oracle result with an explicitly information-free message
  (deliberately NOT "NOT ADDRESSED", which is still semantic evidence). PLACEBO ≈ CLEAN → the harm
  is turn-count/deliberation, not verification content. The queries_used parity check backs this
  reading (a judge fed useless answers might quit querying earlier).
- **na_only / doubled_only** — single-bug decomposition: which oracle bug did the damage, and do
  they interact (is BOTH worse than the sum of the parts)? NA→NO manufactures false negative
  evidence against whichever debater's claim was checked; doubling garbles the question but may
  still be answerable by a charitable oracle.
- **legacy** — replay-fidelity QA, not science: both bugs + pilot exact-string DONE + pilot parser
  primary + the pilot's per-budget A/B re-randomization, on a world-stratified 100-transcript
  subset (K=1). If legacy reproduces pilot-like win rates, the replay machinery behind the BOTH arm
  is validated; if not, BOTH−CLEAN cannot be read as "the effect of the bugs".

Measurement-side pilot bugs (default-to-Position-B verdict coercion, int(raw[0]) confidence) are
handled by dual-parsing every raw verdict in every arm (strict + pilot-compat) at zero API cost;
the arms vary only the treatment side — what the judge actually experiences.

## Pre-registered gates (ex-ante: frozen before any clean data exists)

- **Primary (CLEAN):** Δfew = ½[p(1)+p(2)] − p(0) on strict-parsed verdicts (INVALID excluded and
  reported), question-cluster bootstrap (B=10,000, seeded). Δfew ≥ 4 pp with 95% CI excluding 0 →
  **limited-verification harm survives**. Δfew ≤ 2 pp with CI including 0 → **mostly harness
  artifact**. 2–4 pp → indeterminate (escalate replicates to K=3 before re-judging the gate).
- **Attribution:** BOTH−CLEAN ≥ 3.5 pp, or bugs explain > 50% of the original +7.2 pp → the pilot
  headline was mostly harness-induced.
- **Deliberation:** |PLACEBO − CLEAN| ≤ 2 pp (and PLACEBO−p(0) ≥ 4 pp) → the harm is
  deliberation/turn-count, not verification content.
- **Secondary (reported, not gated):** Δrecover5, dual-parse disagreement rate, INVALID rate,
  well_formed_claim rate, single-bug decomposition (NA_ONLY, DOUBLED_ONLY), legacy-vs-pilot
  agreement (QA only), CLEAN-vs-PLACEBO queries_used distribution parity (turn-count check
  backing the deliberation gate).

## Amendment 1 (2026-07-08, frozen BEFORE the K=3 escalation data)

Stage-1 K=2 outcome: primary gate INDETERMINATE (clean Δfew = +3.54 pp, CI [+1.42, +6.06]).
Per protocol, CLEAN escalates to K=3; PLACEBO is escalated alongside it because the
clean-vs-placebo contrast (content vs deliberation) is the unresolved question.
The original ≥4 pp gate keeps its meaning and is NOT rewritten. Amended follow-up decision
rule for smaller real effects, committed before seeing any K=3 data: if pooled K=3 CLEAN
Δfew ≥ 3 pp with CI lower bound > 0, AND clean−placebo at budgets {1,2} remains positive,
proceed to a REDUCED-SCOPE phase-2 capability pilot (not the full grid). A result below
that is reported as-is with no phase-2 commitment.

## Spend control

Live runs require `--approved-cap` (hard abort on projected overrun). Estimated Stage 1
(all five core arms, K=2, plus legacy subset): ~$185–230 at $1.04/M.

**Launch record (2026-07-08):** account price verified live at $1.04/M (input = output) for
`meta-llama/Llama-3.3-70B-Instruct-Turbo`; Together account funded $500 from the Manifund
grant tranche; total authorized cap **$300** ($10 canary + $290 main run — caps are
per-invocation, so the split keeps the cumulative bound at $300). Project approval by the
lead 2026-07-08; run executed by J. Maiorino.
"""


def main():
    Path("docs/rejudge-protocol.md").write_text(render(), encoding="utf-8")
    print("Wrote docs/rejudge-protocol.md")


if __name__ == "__main__":
    main()
