"""Hardness probe, step 1: author and validate eight worlds under the hardness-v1 prompt.

The canary's k0 error was 2.9%, under the 10% stop rule. This probe tests whether the
proposed author amendment produces harder questions that still pass the frozen
four-validator retention rule. Probe worlds use H-numbered IDs in their own directory and
store, so nothing here touches the W-numbered benchmark or its stage stores. Approved by
Jack on 2026-10-11 (about $10 to $20 expected, hard cap PROBE_CAP).
"""
from __future__ import annotations

import collections
import json
from collections.abc import Hashable
from pathlib import Path
from typing import Any

from . import authoring as A
from . import prompts_authoring as P
from . import validation as V
from .store import Store

PROBE_NAME = "probe-hardness-v1"
PROBE_STAGE = "probe"
PROBE_CAP = 30.0
PROBE_WORLDS = 8
# Seed hints from the second half of the list, so the probe does not reuse the first
# settings the canary worlds drew from.
SEED_OFFSET = 8


def probe_root(run_root: Path) -> Path:
    return run_root / PROBE_NAME


def world_plan() -> list[tuple[str, str, str]]:
    """(world_id, author, seed_hint): H001-H008, authors alternating so each gets four."""
    out = []
    for i in range(PROBE_WORLDS):
        author = ("fable", "astra")[i % 2]
        hint = P.SEED_HINTS[(SEED_OFFSET + i // 2) % len(P.SEED_HINTS)]
        out.append((f"H{i + 1:03d}", author, hint))
    return out


def author_request(world_id: str, author: str, hint: str, attempt: int):
    return A.author_request(world_id, author, hint, attempt=attempt, prompt_revision=P.AUTHOR_HARDNESS_REVISION)


def open_store(root: Path) -> Store:
    return Store(probe_root(root) / "probe.db", PROBE_STAGE, PROBE_CAP, max_transport_attempts=1)


def author(root: Path, mode: str = "batch", attempts: int = 2) -> dict:
    """Author the probe worlds; each attempt reserves its own worst case against PROBE_CAP."""
    base = probe_root(root)
    (base / "worlds").mkdir(parents=True, exist_ok=True)
    s = open_store(root)
    try:
        todo = [p for p in world_plan() if not (base / "worlds" / f"{p[0]}.json").exists()]
        for attempt in range(attempts):
            if not todo:
                break
            reqs = {author_request(w, a, h, attempt).custom_id: (w, a, h) for w, a, h in todo}
            out = s.run([author_request(w, a, h, attempt) for w, a, h in todo],
                        mode=mode, workers=8, allow_live_fallback=False)
            retry = []
            for cid, (w, a, h) in reqs.items():
                r = out.get(cid)
                obj = A.parse_json_object(r.text) if r and r.status == "ok" else None
                chk = A.world_check(obj)
                if not chk.ok:
                    with open(base / "author_rejections.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"world_id": w, "author_call": cid, "attempt": attempt,
                                            "provider_status": r.status if r else "no_response",
                                            "reasons": chk.reasons}) + "\n")
                    print(f"{w} ({a}) attempt {attempt}: rejected {chk.reasons}", flush=True)
                    retry.append((w, a, h))
                    continue
                assert isinstance(obj, dict)
                obj.update({"world_id": w, "author": a, "seed_hint": h, "author_call": cid, "attempt": attempt,
                            "prompt_revision": P.AUTHOR_HARDNESS_REVISION})
                path = base / "worlds" / f"{w}.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
            todo = retry
        result = {"authored": len(list((base / "worlds").glob("H*.json"))), "unusable": [w for w, _, _ in todo],
                  "probe_spend_usd": s.spent(PROBE_STAGE)}
    finally:
        s.db.close()
    print(json.dumps(result), flush=True)
    return result


def load_worlds(root: Path) -> list[dict]:
    worlds = []
    for p in sorted((probe_root(root) / "worlds").glob("H*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if not A.world_check(obj).ok:
            raise ValueError(f"invalid saved probe world {p.name}")
        worlds.append(obj)
    return worlds


def validate(root: Path, mode: str = "batch") -> dict:
    """Frozen validation and retention rule, one world per dispatch to keep reservations small."""
    base = probe_root(root)
    worlds = load_worlds(root)
    if not worlds:
        raise RuntimeError("no probe worlds to validate")
    s = open_store(root)
    rows: list[dict] = []
    out = {}
    try:
        for w in worlds:
            w_rows, reqs = V.workload([w])
            rows.extend(w_rows)
            out.update(s.run(reqs, mode=mode, workers=8, allow_live_fallback=False))
            print(f"{w['world_id']}: {len(reqs)} validation calls, probe spend ${s.spent(PROBE_STAGE):.2f}", flush=True)
        spend = s.spent(PROBE_STAGE)
    finally:
        s.db.close()
    by_world = {w["world_id"]: w for w in worlds}
    for r in rows:
        q = by_world[r["world_id"]]["questions"][r["index"]]
        r["task_type"] = q["task_type"]
        if not r["mechanical_ok"]:
            r.update({"retained": False, "reasons": ["mechanical"] + r["mechanical_reasons"]})
            continue
        d = A.retain_decision(r["question_id"], r["author"], out, len(q["facts_required"]))
        flags = sum(1 for v in A.validators_for(r["author"]) for o in ("key_a", "key_b")
                    if (x := out.get(f"validate:{r['question_id']}:{v}:{o}"))
                    and A.parse_validation(x.text)["reversing_reading"] == "yes")
        r.update({"retained": d["retained"], "reasons": d["reasons"], "determinacy_flags": flags,
                  "key_longer": len(q["correct_answer"].split()) > len(q["wrong_answer"].split())})
    with open(base / "validation.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kept = [r for r in rows if r["retained"]]
    summary = {
        "prompt_revision": P.AUTHOR_HARDNESS_REVISION,
        "candidates": len(rows), "retained": len(kept),
        "retention_rate": len(kept) / len(rows),
        "retained_by_author": dict(collections.Counter(r["author"] for r in kept)),
        "candidates_by_author": dict(collections.Counter(r["author"] for r in rows)),
        "determinacy_flags_retained": dict(collections.Counter(r["determinacy_flags"] for r in kept)),
        "reject_reasons": dict(collections.Counter(x.split(":")[0] for r in rows if not r["retained"]
                                                   for x in r["reasons"])),
        "probe_spend_usd": spend,
    }
    baseline = root / "bench" / "validation.jsonl"
    if baseline.exists():
        b = [json.loads(x) for x in baseline.read_text(encoding="utf-8").splitlines() if x.strip()]
        summary["benchmark_retention_rate"] = sum(r["retained"] for r in b) / len(b)
    (base / "validation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


# ------------------------------------------------------------------ step 2: mini canary

CANARY_STAGE = "probe_canary"
CANARY_CAP = 45.0
CANARY_PER_AUTHOR = 10


def canary_questions(root: Path, per_author: int = CANARY_PER_AUTHOR) -> list[dict]:
    """Retained probe questions, the first per_author of each author in fixed hash order."""
    by_world = {w["world_id"]: w for w in load_worlds(root)}
    rows = [json.loads(x) for x in (probe_root(root) / "validation.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    picked = []
    for author in ("fable", "astra"):
        kept = sorted((r for r in rows if r["retained"] and r["author"] == author),
                      key=lambda r: A.hashlib.sha256(f"probe-canary-v1:{r['question_id']}".encode()).hexdigest())
        for r in kept[:per_author]:
            q = dict(by_world[r["world_id"]]["questions"][r["index"]])
            q.update({"question_id": r["question_id"], "world_id": r["world_id"], "author": author,
                      "determinacy_flags": r["determinacy_flags"]})
            picked.append(q)
    return picked


def canary(root: Path, judges: tuple[str, ...], debaters: tuple[str, ...], workers: int = 16) -> dict:
    """Uncapped debates by both debaters, judged at k0 by the canary panel in both orders."""
    from .debate import debate_task
    from .engine import drive
    from .judging import ORDERS, judge_task

    base = probe_root(root) / "canary"
    base.mkdir(parents=True, exist_ok=True)
    worlds = {w["world_id"]: w for w in load_worlds(root)}
    qs = {q["question_id"]: q for q in canary_questions(root)}
    s = Store(probe_root(root) / "probe.db", CANARY_STAGE, CANARY_CAP, max_transport_attempts=6)
    try:
        tasks: dict[Hashable, Any] = {(qid, deb): debate_task(q, worlds[q["world_id"]]["world_text"], deb, None, "uncapped")
                 for qid, q in qs.items() for deb in debaters}
        debates, failed = drive(s, tasks, workers=workers)
        jt: dict[Hashable, Any] = {}
        for d in debates.values():
            qid, deb = d["question_id"], d["debater"]
            item = dict(qs[qid], world_text=worlds[qs[qid]["world_id"]]["world_text"], turns=d["turns"],
                        question_id=f"{qid}:{deb}:uncapped")
            for j in judges:
                for o in ORDERS:
                    jt[(qid, deb, j, o)] = judge_task(item, j, "debate_k0", o, "opus", "dsflash")
        judged, jfailed = drive(s, jt, workers=workers)
        spend = s.spent(CANARY_STAGE)
    finally:
        s.db.close()
    rows = []
    for r in judged.values():
        qid, deb = r["question_id"].split(":")[:2]
        rows.append(dict(r, debater=deb, probe_question=qid, determinacy_flags=qs[qid]["determinacy_flags"]))
    for name, data in (("debates.jsonl", debates.values()), ("judgments.jsonl", rows)):
        with open(base / name, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
    cells: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        for key in (f"judge:{r['judge']}", f"debater:{r['debater']}", "pooled",
                    f"flags:{min(r['determinacy_flags'], 2)}{'+' if r['determinacy_flags'] >= 2 else ''}"):
            cells[key][0] += not r["correct"]
            cells[key][1] += 1
    summary = {"questions": len(qs), "debates": len(debates), "failed_debates": len(failed),
               "judgments": len(rows), "failed_judgments": len(jfailed),
               "parse_failures": sum(not r["parse_ok"] for r in rows),
               "k0_error": {k: {"wrong": w, "n": n, "rate": w / n} for k, (w, n) in sorted(cells.items())},
               "stop_rule_threshold": 0.10, "canary_reference_pooled": 0.029,
               "probe_canary_spend_usd": spend}
    (base / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return summary
