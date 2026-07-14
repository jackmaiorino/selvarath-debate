"""Calibration analysis: prints the pre-declared readout for the calibration run.

Reads the per-judge output of `rejudge/calibrate.py`
(`rejudge/output/calibration_judgments_{judgeshort}.jsonl`) plus the source
`calibration_transcripts.jsonl` / `calibration_models.json`, and prints:

  1. A b0 table (judge x protocol x debater): wrong rate, mirrored side effect
     (|wrong rate at design assignment - wrong rate at mirrored assignment|, the <10pp
     gate), strict-INVALID rate.
  2. The protocol-selection readout: anchor judge x l70 debater b0, capped3 vs uncapped3
     wrong rates.
  3. Guardrail PASS/FAIL lines: low-judge b0 in [20,35]%? top-judge b0 in [3,10]% (not
     floor)? strict-INVALID <2% everywhere (b0)? mirrored side effect <10pp everywhere?
  4. b2 cell stats per judge (smoke cells for the three non-anchor judges, saturation
     cells for the anchor judge -- same reporting shape either way, distinguished by
     record count): INVALID rate, mean queries_used, count of prohibited-style queries
     (>70% word overlap with either candidate answer), count of judged transcripts with
     a word_cap_violated turn.

No gate auto-decides anything -- this prints the table and a PASS/FAIL line per
pre-declared criterion and leaves the selection to a human.

Note on the prohibited-style-query check: the brief that specified this analysis says to
"port the check from the shortcut audit" (docs/oracle-query-contract.md's ">70% word
overlap" rule). That audit script is not present in this repository (not committed, no
trace in git history) -- `word_overlap` below is a reconstruction from the documented
behavior ("high word overlap with a candidate answer"), not a byte-for-byte port. It is
defined as the overlap coefficient: the fraction of the CLAIM's own tokens that also
appear in the candidate answer's token set (case-insensitive, alphanumeric tokens only).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from rejudge import calibrate

OVERLAP_THRESHOLD = 0.70

LOW_BAND = (0.20, 0.35)
TOP_BAND = (0.03, 0.10)
INVALID_CEILING = 0.02
SIDE_EFFECT_CEILING = 0.10

_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def word_overlap(claim: str, candidate: str) -> float:
    """Overlap coefficient: |claim tokens ∩ candidate tokens| / |claim tokens|."""
    claim_words = _tokenize(claim)
    if not claim_words:
        return 0.0
    candidate_words = _tokenize(candidate)
    return len(claim_words & candidate_words) / len(claim_words)


def is_prohibited_style(claim: str, correct_answer: str, wrong_answer: str,
                        threshold: float = OVERLAP_THRESHOLD) -> bool:
    return (word_overlap(claim, correct_answer) > threshold
           or word_overlap(claim, wrong_answer) > threshold)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_jsonl_tolerant(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def load_judgments(out_dir, judge_keys) -> dict:
    result = {}
    for jk in judge_keys:
        short = calibrate.JUDGE_SHORT[jk]
        path = Path(out_dir) / f"calibration_judgments_{short}.jsonl"
        result[jk] = _load_jsonl_tolerant(path)
    return result


def load_transcripts_index(path) -> dict:
    """(protocol, debater_model, question_id, transcript_index) -> transcript record."""
    return {(tr["protocol"], tr["debater_model"], tr["question_id"], tr["transcript_index"]): tr
           for tr in calibrate.load_transcripts(path)}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def wrong_rate(recs: list[dict]):
    valid = [r for r in recs if r.get("verdict_correct_strict") is not None]
    if not valid:
        return None
    return sum(1 for r in valid if r["verdict_correct_strict"] is False) / len(valid)


def invalid_rate(recs: list[dict]):
    if not recs:
        return None
    return sum(1 for r in recs if r.get("verdict_correct_strict") is None) / len(recs)


def b0_rows(judgments_by_judge: dict) -> list[dict]:
    rows = []
    for judge_key, recs in judgments_by_judge.items():
        b0 = [r for r in recs if r.get("budget") == 0]
        groups: dict = {}
        for r in b0:
            groups.setdefault((r["protocol"], r["debater_model"]), []).append(r)
        for (protocol, debater), grecs in groups.items():
            design = [r for r in grecs if not r.get("mirrored")]
            mirrored = [r for r in grecs if r.get("mirrored")]
            wr_design = wrong_rate(design)
            wr_mirrored = wrong_rate(mirrored)
            side_effect = (abs(wr_design - wr_mirrored)
                          if wr_design is not None and wr_mirrored is not None else None)
            rows.append({"judge": judge_key, "protocol": protocol, "debater_model": debater,
                        "n": len(grecs), "wrong_rate": wrong_rate(grecs),
                        "wrong_rate_design": wr_design, "wrong_rate_mirrored": wr_mirrored,
                        "side_effect": side_effect, "invalid_rate": invalid_rate(grecs)})
    return rows


def protocol_selection_readout(rows: list[dict], l70_model: str) -> dict:
    anchor_l70 = [r for r in rows if r["judge"] == "anchor" and r["debater_model"] == l70_model]
    capped = next((r for r in anchor_l70 if r["protocol"] == "capped3"), None)
    uncapped = next((r for r in anchor_l70 if r["protocol"] == "uncapped3"), None)
    return {"capped3_wrong_rate": capped["wrong_rate"] if capped else None,
           "uncapped3_wrong_rate": uncapped["wrong_rate"] if uncapped else None}


def guardrail_checks(rows: list[dict]) -> list[dict]:
    low_rows = [r for r in rows if r["judge"] in ("low_primary", "low_fallback")]
    low_pass = (all(r["wrong_rate"] is not None and LOW_BAND[0] <= r["wrong_rate"] <= LOW_BAND[1]
                    for r in low_rows) if low_rows else None)
    top_rows = [r for r in rows if r["judge"] == "top"]
    top_pass = (all(r["wrong_rate"] is not None and TOP_BAND[0] <= r["wrong_rate"] <= TOP_BAND[1]
                    for r in top_rows) if top_rows else None)
    invalid_pass = (all(r["invalid_rate"] is not None and r["invalid_rate"] < INVALID_CEILING
                        for r in rows) if rows else None)
    side_pass = (all(r["side_effect"] is not None and r["side_effect"] < SIDE_EFFECT_CEILING
                     for r in rows) if rows else None)
    return [
        {"name": f"low-judge b0 wrong rate in [{int(100*LOW_BAND[0])},{int(100*LOW_BAND[1])}]%",
         "pass": low_pass, "rows": low_rows},
        {"name": f"top-judge b0 wrong rate in [{int(100*TOP_BAND[0])},{int(100*TOP_BAND[1])}]% "
                 "(not floor)", "pass": top_pass, "rows": top_rows},
        {"name": f"strict-INVALID rate <{int(100*INVALID_CEILING)}% everywhere (b0)",
         "pass": invalid_pass, "rows": rows},
        {"name": f"mirrored side effect <{int(100*SIDE_EFFECT_CEILING)}pp everywhere (b0)",
         "pass": side_pass, "rows": rows},
    ]


def b2_rows(judgments_by_judge: dict, transcripts_by_key: dict) -> list[dict]:
    rows = []
    for judge_key, recs in judgments_by_judge.items():
        b2 = [r for r in recs if r.get("budget") == 2]
        if not b2:
            continue
        n = len(b2)
        prohibited = 0
        violated_transcripts: set = set()
        for r in b2:
            key = (r["protocol"], r["debater_model"], r["question_id"], r["transcript_index"])
            tr = transcripts_by_key.get(key)
            if tr is None:
                continue
            for ex in r.get("exchanges", []):
                claim = ex.get("extracted_claim")
                if claim and is_prohibited_style(claim, tr["correct_answer"], tr["wrong_answer"]):
                    prohibited += 1
            if any(t.get("word_cap_violated") for t in tr.get("debate_transcript", [])):
                violated_transcripts.add(key)
        rows.append({"judge": judge_key, "n": n, "invalid_rate": invalid_rate(b2),
                    "mean_queries_used": sum(r.get("queries_used", 0) for r in b2) / n,
                    "prohibited_query_count": prohibited,
                    "word_cap_violated_transcripts": len(violated_transcripts)})
    return rows


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _fmt_pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _fmt_pass(p):
    return "n/a (no data)" if p is None else ("PASS" if p else "FAIL")


def print_report(judgments_by_judge: dict, transcripts_by_key: dict, l70_model: str) -> None:
    rows = b0_rows(judgments_by_judge)

    print("== b0 (judge x protocol x debater): wrong rate, mirrored side effect, INVALID rate ==")
    for r in sorted(rows, key=lambda r: (r["judge"], r["protocol"], r["debater_model"])):
        print(f"  {r['judge']:12s} {r['protocol']:10s} {r['debater_model']:45s} n={r['n']:3d} "
              f"wrong={_fmt_pct(r['wrong_rate'])}  "
              f"design={_fmt_pct(r['wrong_rate_design'])} mirrored={_fmt_pct(r['wrong_rate_mirrored'])} "
              f"side_effect={_fmt_pct(r['side_effect'])}  INVALID={_fmt_pct(r['invalid_rate'])}")

    print("\n== protocol selection cell: anchor x l70 debater, b0 ==")
    readout = protocol_selection_readout(rows, l70_model)
    print(f"  capped3 wrong rate:   {_fmt_pct(readout['capped3_wrong_rate'])}")
    print(f"  uncapped3 wrong rate: {_fmt_pct(readout['uncapped3_wrong_rate'])}")

    print("\n== guardrails (no gate auto-decides; human call) ==")
    for check in guardrail_checks(rows):
        print(f"  [{_fmt_pass(check['pass'])}] {check['name']}")

    b2 = b2_rows(judgments_by_judge, transcripts_by_key)
    print("\n== b2 cells (smoke: 3 non-anchor judges; saturation: anchor) ==")
    for r in sorted(b2, key=lambda r: r["judge"]):
        print(f"  {r['judge']:12s} n={r['n']:3d}  INVALID={_fmt_pct(r['invalid_rate'])}  "
              f"mean_queries_used={r['mean_queries_used']:.2f}  "
              f"prohibited_style_queries={r['prohibited_query_count']}  "
              f"word_cap_violated_transcripts={r['word_cap_violated_transcripts']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=calibrate.DEFAULT_OUT_DIR)
    ap.add_argument("--transcripts", default=calibrate.DEFAULT_TRANSCRIPTS)
    ap.add_argument("--models", default=calibrate.DEFAULT_MODELS)
    args = ap.parse_args(argv)

    models_cfg = calibrate.load_calibration_models(args.models)
    l70_model = calibrate.find_debater_model(models_cfg, "l70")
    judgments_by_judge = load_judgments(args.out_dir, list(calibrate.JUDGE_SHORT))
    transcripts_by_key = load_transcripts_index(args.transcripts)

    print_report(judgments_by_judge, transcripts_by_key, l70_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
