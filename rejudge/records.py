"""Output record construction with full provenance."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def get_git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


_GIT_SHA = None


def _sha() -> str:
    global _GIT_SHA
    if _GIT_SHA is None:
        _GIT_SHA = get_git_sha()
    return _GIT_SHA


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_record(*, transcript, arm, budget, replicate, position_a_is_correct,
                 exchanges, raw_verdict_text, parses, judge_messages, seed,
                 judge_model, oracle_model, dry_run, queries_used) -> dict:
    strict = parses["strict"]
    pilot = parses["pilot"]
    if strict["verdict"] is None:
        correct_strict = None
    else:
        correct_strict = (strict["verdict"] == "A") == position_a_is_correct
    correct_pilot = (pilot["verdict"] == "Position A") == position_a_is_correct
    qid = transcript["question_id"]
    tidx = transcript["transcript_index"]
    return {
        "question_id": qid,
        "transcript_index": tidx,
        "world": transcript.get("world"),
        "arm": arm.name,
        "budget": budget,
        "replicate": replicate,
        "cell_key": f"{arm.name}|{qid}|{tidx}|{budget}|{replicate}",
        "position_a_is_correct": position_a_is_correct,
        "queries_used": queries_used,
        "exchanges": exchanges,
        "raw_verdict_text": raw_verdict_text,
        "verdict_strict": strict,
        "verdict_pilot": pilot,
        "verdict_correct_strict": correct_strict,
        "verdict_correct_pilot": correct_pilot,
        "judge_messages": judge_messages,
        "seed": seed,
        "judge_model": judge_model,
        "oracle_model": oracle_model,
        "parser_version": parses["parser_version"],
        "harness_version": _sha(),
        "dry_run": dry_run,
        "created_at": utc_now_iso(),
    }
