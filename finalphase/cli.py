"""Stage commands for the final phase.

  python -m finalphase.cli author   [--worlds 160] [--only W001,W002] [--mode batch]
  python -m finalphase.cli validate [--mode batch]
  python -m finalphase.cli split
  python -m finalphase.cli canary
  python -m finalphase.cli judge --split pilot|main [--mode batch]

Hot state lives under the run root (SSD); the benchmark and results are copied to the
archive at stage closure. Every stage has its own spend cap in STAGE_CAPS.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path

from . import authoring as A
from . import prompts_study as P
from .debate import debate_task
from .engine import drive
from .judging import ARMS, ORDERS, judge_task, parse_selector, selector_request
from .providers import Request
from .store import Store

RUN_ROOT = Path(os.environ.get("FINALPHASE_ROOT", "D:/finalphase-runs/final-phase-2026-09-30"))
BENCH = RUN_ROOT / "bench"
STAGE_CAPS = {"author": 250.0, "validate": 200.0, "canary": 60.0, "pilot": 650.0, "main": 4300.0}
JUDGES = ("luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70", "qwen38")
CANARY_JUDGES = ("luna", "haiku", "llama70")
DEBATERS = ("fable", "astra")


def _store(stage: str) -> Store:
    return Store(RUN_ROOT / f"{stage}.db", stage, STAGE_CAPS[stage])


def _progress(rnd, pending, done):
    print(f"  round {rnd}: {pending} tasks pending, {done} finished", flush=True)


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]


# ------------------------------------------------------------------ stage 2

def cmd_author(args) -> None:
    s = _store("author")
    plan = A.world_ids(args.worlds)
    if args.only:
        keep = set(args.only.split(","))
        plan = [p for p in plan if p[0] in keep]
    (BENCH / "worlds").mkdir(parents=True, exist_ok=True)
    todo = [p for p in plan if not (BENCH / "worlds" / f"{p[0]}.json").exists()]
    for attempt in (0, 1):
        if not todo:
            break
        reqs = {A.author_request(w, a, h, attempt=attempt).custom_id: (w, a, h) for w, a, h in todo}
        out = s.run([A.author_request(w, a, h, attempt=attempt) for w, a, h in todo], mode=args.mode, workers=8)
        retry = []
        for cid, (w, a, h) in reqs.items():
            r = out.get(cid)
            obj = A.parse_json_object(r.text) if r and r.status == "ok" else None
            if not obj or not obj.get("world_text") or not obj.get("questions"):
                print(f"{w} ({a}) attempt {attempt}: unusable ({r.status if r else 'no response'})")
                retry.append((w, a, h))
                continue
            obj.update({"world_id": w, "author": a, "seed_hint": h, "author_call": cid, "attempt": attempt})
            (BENCH / "worlds" / f"{w}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
        todo = retry
    print(f"authored {len(list((BENCH / 'worlds').glob('*.json')))} worlds; unusable after retry: {[w for w, _, _ in todo]}; "
          f"stage spend ${s.spent('author'):.2f}")


def _worlds() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((BENCH / "worlds").glob("W*.json"))]


def cmd_validate(args) -> None:
    s = _store("validate")
    worlds = _worlds()
    rows, reqs = [], []
    for w in worlds:
        for i, q in enumerate(w["questions"]):
            chk = A.mechanical_check(q, w["world_text"], w["questions"])
            rows.append({"question_id": A.qid(w["world_id"], i), "world_id": w["world_id"], "author": w["author"],
                         "index": i, "mechanical_ok": chk.ok, "mechanical_reasons": chk.reasons})
        ok_qs = [q for i, q in enumerate(w["questions"]) if A.mechanical_check(q, w["world_text"], w["questions"]).ok]
        ok_idx = [i for i, q in enumerate(w["questions"]) if A.mechanical_check(q, w["world_text"], w["questions"]).ok]
        for req in A.validation_requests(w["world_id"], w["author"], w["world_text"], w["questions"]):
            qi = int(req.custom_id.split(":")[1].split("-Q")[1]) - 1
            if qi in ok_idx:
                reqs.append(req)
    print(f"{len(rows)} candidates, {sum(r['mechanical_ok'] for r in rows)} pass mechanical checks, {len(reqs)} validation calls")
    out = s.run(reqs, mode=args.mode, workers=16)
    by_world = {w["world_id"]: w for w in worlds}
    for r in rows:
        if not r["mechanical_ok"]:
            r.update({"retained": False, "reasons": ["mechanical"] + r["mechanical_reasons"]})
            continue
        q = by_world[r["world_id"]]["questions"][r["index"]]
        d = A.retain_decision(r["question_id"], r["author"], out, len(q["facts_required"]))
        flags = 0
        for v in A.validators_for(r["author"]):
            for o in ("key_a", "key_b"):
                x = out.get(f"validate:{r['question_id']}:{v}:{o}")
                if x and A.parse_validation(x.text)["reversing_reading"] == "yes":
                    flags += 1
        a, b = len(q["correct_answer"].split()), len(q["wrong_answer"].split())
        r.update({"retained": d["retained"], "reasons": d["reasons"], "determinacy_flags": flags,
                  "key_longer": a > b, "task_type": q["task_type"]})
    _write_jsonl(BENCH / "validation.jsonl", rows)
    ret = [r for r in rows if r["retained"]]
    print(f"retained {len(ret)}/{len(rows)}; by author {collections.Counter(r['author'] for r in ret)}; "
          f"stage spend ${s.spent('validate'):.2f}")


def cmd_split(args) -> None:
    worlds = _worlds()
    rows = _read_jsonl(BENCH / "validation.jsonl")
    split = A.split_worlds([{"world_id": w["world_id"], "author": w["author"]} for w in worlds])
    by_world = {w["world_id"]: w for w in worlds}
    retained = []
    for r in rows:
        if r["retained"]:
            q = dict(by_world[r["world_id"]]["questions"][r["index"]])
            q.update({"question_id": r["question_id"], "world_id": r["world_id"], "author": r["author"],
                      "split": split[r["world_id"]], "determinacy_flags": r["determinacy_flags"]})
            retained.append(q)
    main = A.sample_main([q for q in retained if q["split"] == "main"])
    sets = {"canary": [q for q in retained if q["split"] == "canary"], "pilot": [q for q in retained if q["split"] == "pilot"], "main": main}
    # debater family per question, balanced within author x task type by a fixed hash order
    for name, qs in sets.items():
        groups = collections.defaultdict(list)
        for q in qs:
            groups[(q["author"], q["task_type"])].append(q)
        for g in groups.values():
            g.sort(key=lambda q: A.hashlib.sha256(f"debater-v1:{q['question_id']}".encode()).hexdigest())
            for i, q in enumerate(g):
                q["debater"] = DEBATERS[i % 2]
        _write_jsonl(BENCH / f"{name}.jsonl", qs)
        print(name, len(qs), collections.Counter(q["debater"] for q in qs))
    (BENCH / "split.json").write_text(json.dumps(split, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ stage 3

def cmd_canary(args) -> None:
    s = _store("canary")
    worlds = {w["world_id"]: w for w in _worlds()}
    qs = _read_jsonl(BENCH / "canary.jsonl")
    qs = sorted(qs, key=lambda q: A.hashlib.sha256(f"canary-v1:{q['question_id']}".encode()).hexdigest())[:args.n]
    tasks = {}
    for q in qs:
        for deb in DEBATERS:
            for cond, cap in (("uncapped", None), ("cap150", 150)):
                tasks[(q["question_id"], deb, cond)] = debate_task(q, worlds[q["world_id"]]["world_text"], deb, cap, cond)
    debates, failed = drive(s, tasks, progress=_progress)
    _write_jsonl(RUN_ROOT / "canary" / "debates.jsonl", debates.values())
    # compliance classifier on every turn
    comp = []
    for (qid, deb, cond), d in debates.items():
        q = next(x for x in qs if x["question_id"] == qid)
        for t in d["turns"]:
            pos = q["correct_answer"] if t["role"] == "honest" else q["wrong_answer"]
            comp.append(Request(f"compliance:{cond}:{deb}:{qid}:r{t['round']}:{t['role']}", "dspro", P.COMPLIANCE_SYSTEM,
                                (("user", P.COMPLIANCE_USER.format(question=q["question"], position=pos, turn=t["text"])),), 8000))
    cout = s.run(comp, workers=16)
    # k0 judgments by the canary judges
    jt = {}
    for (qid, deb, cond), d in debates.items():
        q = dict(next(x for x in qs if x["question_id"] == qid))
        q.update({"world_text": worlds[q["world_id"]]["world_text"], "turns": d["turns"], "question_id": f"{qid}:{deb}:{cond}"})
        for j in CANARY_JUDGES:
            for o in ORDERS:
                jt[(qid, deb, cond, j, o)] = judge_task(q, j, "debate_k0", o, "opus", "dsflash")
    judged, jfailed = drive(s, jt, progress=_progress)
    _write_jsonl(RUN_ROOT / "canary" / "judgments.jsonl", judged.values())
    _write_jsonl(RUN_ROOT / "canary" / "compliance.jsonl",
                 [{"custom_id": r.custom_id, "text": cout[r.custom_id].text if r.custom_id in cout else None} for r in comp])
    print(f"debates {len(debates)} (failed {len(failed)}), judgments {len(judged)} (failed {len(jfailed)}), spend ${s.spent('canary'):.2f}")


# ------------------------------------------------------------------ stages 4-5

def cmd_judge(args) -> None:
    stage = args.split
    s = _store(stage)
    worlds = {w["world_id"]: w for w in _worlds()}
    qs = _read_jsonl(BENCH / f"{stage}.jsonl")
    if args.limit:
        qs = qs[:args.limit]
    tasks = {q["question_id"]: debate_task(q, worlds[q["world_id"]]["world_text"], q["debater"], None, "uncapped") for q in qs}
    debates, failed = drive(s, tasks, mode=args.mode, progress=_progress)
    _write_jsonl(RUN_ROOT / stage / "debates.jsonl", debates.values())
    items = []
    for q in qs:
        if q["question_id"] in debates:
            it = dict(q)
            it.update({"world_text": worlds[q["world_id"]]["world_text"], "turns": debates[q["question_id"]]["turns"]})
            items.append(it)
    arms = [a for a in ARMS if a != "top2" or not args.no_top2]
    top2 = {}
    if "top2" in arms:
        sel = s.run([selector_request(it) for it in items], mode=args.mode)
        top2 = {it["question_id"]: parse_selector(sel[selector_request(it).custom_id].text) for it in items
                if selector_request(it).custom_id in sel}
    jt = {}
    for it in items:
        for j in args.judges.split(","):
            for arm in arms:
                for o in ORDERS:
                    jt[(it["question_id"], j, arm, o)] = judge_task(it, j, arm, o, args.oracle, args.gate, top2.get(it["question_id"]))
    judged, jfailed = drive(s, jt, mode=args.mode, workers=args.workers, progress=_progress)
    _write_jsonl(RUN_ROOT / stage / "judgments.jsonl", judged.values())
    print(f"{stage}: debates {len(debates)}, judgments {len(judged)} (failed {len(jfailed)}), spend ${s.spent(stage):.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("author"); a.add_argument("--worlds", type=int, default=160); a.add_argument("--only"); a.add_argument("--mode", default="batch")
    v = sub.add_parser("validate"); v.add_argument("--mode", default="batch")
    sub.add_parser("split")
    c = sub.add_parser("canary"); c.add_argument("--n", type=int, default=20)
    j = sub.add_parser("judge"); j.add_argument("--split", required=True, choices=("pilot", "main"))
    j.add_argument("--mode", default="batch"); j.add_argument("--judges", default=",".join(JUDGES))
    j.add_argument("--oracle", default="opus"); j.add_argument("--gate", default="dsflash")
    j.add_argument("--no-top2", action="store_true"); j.add_argument("--limit", type=int, default=0); j.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    {"author": cmd_author, "validate": cmd_validate, "split": cmd_split, "canary": cmd_canary, "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    main()
