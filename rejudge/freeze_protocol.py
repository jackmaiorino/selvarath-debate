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

## Spend control

Live runs require `--approved-cap` (hard abort on projected overrun). Estimated Stage 1
(all five core arms, K=2, plus legacy subset): ~$185–230 at $1.04/M; approved cap to be
recorded here at launch alongside the account price.
"""


def main():
    Path("docs/rejudge-protocol.md").write_text(render(), encoding="utf-8")
    print("Wrote docs/rejudge-protocol.md")


if __name__ == "__main__":
    main()
