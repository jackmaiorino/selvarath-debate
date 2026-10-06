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
import sqlite3
from pathlib import Path

from . import authoring as A
from . import prompts_study as P
from . import preflight
from . import validation as V
from .debate import debate_task
from .engine import drive
from .judging import ARMS, ORDERS, judge_task
from .providers import Request
from .store import Store

RUN_ROOT = Path(os.environ.get("FINALPHASE_ROOT", "D:/finalphase-runs/final-phase-2026-10-03"))
BENCH = RUN_ROOT / "bench"
STAGE_CAPS = {"author": 200.0, "validate": 200.0, "canary": 60.0, "pilot": 650.0, "main": 4300.0}
JUDGES = ("luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70", "qwen38")
CANARY_JUDGES = ("luna", "haiku", "llama70")
DEBATERS = ("fable", "astra")


def _store(stage: str) -> Store:
    from .limits import BatchLimits
    return Store(RUN_ROOT / f"{stage}.db", stage, STAGE_CAPS[stage],
                 max_transport_attempts=1 if stage in ("author", "validate") else 6,
                 batch_limits={m: BatchLimits(1500000, 100) for m in DEBATERS} if stage == "author" else None)


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
    try:
        _run_author(args, s)
    finally:
        s.db.close()


def _run_author(args, s: Store) -> None:
    max_tokens = getattr(args, "max_tokens", None)
    attempts = getattr(args, "attempts", 2)
    spend_cap = getattr(args, "spend_cap", None)
    if spend_cap is not None:
        s.cap = min(s.cap, spend_cap)
    plan = A.world_ids(args.worlds)
    if args.only:
        keep = set(args.only.split(","))
        plan = [p for p in plan if p[0] in keep]
        if keep != {p[0] for p in plan}:
            raise ValueError("--only contains unknown world IDs")
    (BENCH / "worlds").mkdir(parents=True, exist_ok=True)
    todo = []
    for w, a, h in plan:
        path = BENCH / "worlds" / f"{w}.json"
        if not path.exists():
            todo.append((w, a, h))
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            obj = None
        chk = A.world_check(obj)
        if chk.ok and isinstance(obj, dict) and (obj.get("world_id"), obj.get("author"), obj.get("seed_hint")) == (w, a, h):
            continue
        print(f"{w}: saved world rejected: {chk.reasons or ['world_identity_mismatch']}", flush=True)
        if not getattr(args, "replace_invalid", False):
            raise RuntimeError(f"invalid saved world {w}; use --replace-invalid to preserve and regenerate it")
        digest = A.hashlib.sha256(path.read_bytes()).hexdigest()
        _preserve_bytes(BENCH / "versions" / digest / path.name, path.read_bytes())
        todo.append((w, a, h))
    # Reserve every permitted generation attempt before the first paid request.
    reserve = [A.author_request(w, a, h, attempt=i, max_tokens=max_tokens)
               for i in range(attempts) for w, a, h in todo]
    s._check_cap([r for r in reserve if s.get(r.custom_id) is None], args.mode == "batch")
    for attempt in range(attempts):
        if not todo:
            break
        reqs = {A.author_request(w, a, h, attempt=attempt, max_tokens=max_tokens).custom_id: (w, a, h) for w, a, h in todo}
        out = s.run([A.author_request(w, a, h, attempt=attempt, max_tokens=max_tokens) for w, a, h in todo],
                    mode=args.mode, workers=8, allow_live_fallback=False)
        retry = []
        for cid, (w, a, h) in reqs.items():
            r = out.get(cid)
            obj = A.parse_json_object(r.text) if r and r.status == "ok" else None
            chk = A.world_check(obj)
            if not chk.ok:
                rejection = {"world_id": w, "author_call": cid, "attempt": attempt,
                             "provider_status": r.status if r else "no_response", "reasons": chk.reasons}
                with open(RUN_ROOT / "author_rejections.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rejection) + "\n")
                print(f"{w} ({a}) attempt {attempt}: rejected {rejection}", flush=True)
                retry.append((w, a, h))
                continue
            assert isinstance(obj, dict)
            obj.update({"world_id": w, "author": a, "seed_hint": h, "author_call": cid, "attempt": attempt})
            data = json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8")
            _preserve_bytes(BENCH / "versions" / A.hashlib.sha256(cid.encode()).hexdigest() / f"{w}.json", data)
            path = BENCH / "worlds" / f"{w}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        todo = retry
    print(f"authored {len(list((BENCH / 'worlds').glob('*.json')))} worlds; unusable after retry: {[w for w, _, _ in todo]}; "
          f"stage spend ${s.spent('author'):.2f}")
    if todo:
        raise RuntimeError(f"world authoring incomplete after {attempts} attempts: {[w for w, _, _ in todo]}")


def _preserve_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"refusing to change preserved artifact {path}")
    else:
        path.write_bytes(data)


def _worlds() -> list[dict]:
    worlds = []
    for p in sorted((BENCH / "worlds").glob("W*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        chk = A.world_check(obj)
        if not chk.ok:
            raise ValueError(f"invalid saved world {p.name}: {chk.reasons}")
        worlds.append(obj)
    return worlds


def cmd_validate(args) -> None:
    worlds = _worlds()
    reservation = V.require_plan(RUN_ROOT, worlds, args.mode)
    V.atomic_json(RUN_ROOT / "validation_reservation.json", reservation)
    rows, reqs = V.workload(worlds)
    s = Store(RUN_ROOT / "validate.db", "validate", STAGE_CAPS["validate"],
              max_transport_attempts=1, batch_limits=V.dispatch_limits(RUN_ROOT))
    print(f"{len(rows)} candidates, {sum(r['mechanical_ok'] for r in rows)} pass mechanical checks, {len(reqs)} validation calls")
    V.execution_status(RUN_ROOT, "validation_running", pid=os.getpid(), workers=args.workers,
                       execution_started=True)
    try:
        out = s.run(reqs, mode=args.mode, workers=args.workers, allow_live_fallback=False)
        spend = s.spent("validate")
    except BaseException as e:
        V.execution_status(RUN_ROOT, "validation_needs_attention", error=f"{type(e).__name__}: {e}",
                           stage_spend_usd=s.spent("validate"))
        raise
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
    from .reporting import write_report
    write_report(RUN_ROOT, rows, worlds, out, len(out) == len(reqs), spend)
    ret = [r for r in rows if r["retained"]]
    print(f"retained {len(ret)}/{len(rows)}; by author {collections.Counter(r['author'] for r in ret)}; "
          f"responses {len(out)}/{len(reqs)}, stage spend ${spend:.6f}")
    V.execution_status(RUN_ROOT, "validation_complete" if len(out) == len(reqs) else "validation_needs_attention",
                       stage_spend_usd=spend, responses=len(out), requests=len(reqs), retained=len(ret),
                       independent_answer_validation_complete=len(out) == len(reqs), audit_complete=False)
    if len(out) != len(reqs):
        raise RuntimeError("validation incomplete; preserve partial results and reserve any paid retry before resuming")


def cmd_validation_plan(args) -> None:
    report = V.plan(RUN_ROOT, _worlds(), args.mode)
    print(json.dumps(report, indent=2))


def cmd_qualify_validate(args) -> None:
    print(json.dumps(V.qualify(RUN_ROOT, _worlds(), args.mode), indent=2))


def cmd_split(args) -> None:
    # Cost-driven sample reductions happen before any main requests are registered.
    main_db = RUN_ROOT / "main.db"
    if main_db.exists():
        with sqlite3.connect(main_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]:
                raise ValueError("cannot change the benchmark split after main requests have been registered")
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
    main = A.sample_main([q for q in retained if q["split"] == "main"], cap=args.main_questions)
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
    jt = {}
    for it in items:
        for j in args.judges.split(","):
            for arm in ARMS:
                for o in ORDERS:
                    jt[(it["question_id"], j, arm, o)] = judge_task(it, j, arm, o, args.oracle, args.gate)
    judged, jfailed = drive(s, jt, mode=args.mode, workers=args.workers, progress=_progress)
    _write_jsonl(RUN_ROOT / stage / "judgments.jsonl", judged.values())
    print(f"{stage}: debates {len(debates)}, judgments {len(judged)} (failed {len(jfailed)}), spend ${s.spent(stage):.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("author"); a.add_argument("--worlds", type=int, default=160); a.add_argument("--only"); a.add_argument("--mode", default="batch", choices=("batch", "live"))
    a.add_argument("--quality-check", action="store_true", help="bounded authoring check, at most eight worlds")
    a.add_argument("--max-tokens", type=int, choices=(32000, 64000), help="override author defaults: Fable 64000, Astra 32000")
    a.add_argument("--attempts", type=int, choices=(1, 2), default=2)
    a.add_argument("--spend-cap", type=float, help="lower the cumulative author-stage cap for a bounded quality probe")
    a.add_argument("--replace-invalid", action="store_true", help="preserve invalid saved worlds and regenerate under fresh versioned IDs")
    v = sub.add_parser("validate"); v.add_argument("--mode", default="batch", choices=("batch", "live"))
    v.add_argument("--workers", type=int, choices=(1, 8), default=8)
    qp = sub.add_parser("qualify-validate"); qp.add_argument("--mode", default="batch", choices=("batch", "live"))
    vp = sub.add_parser("validation-plan"); vp.add_argument("--mode", default="batch", choices=("batch", "live"))
    sp = sub.add_parser("split"); sp.add_argument("--main-questions", type=int, default=1068)
    c = sub.add_parser("canary"); c.add_argument("--n", type=int, default=20)
    j = sub.add_parser("judge"); j.add_argument("--split", required=True, choices=("pilot", "main"))
    j.add_argument("--mode", default="batch", choices=("batch", "live")); j.add_argument("--judges", default=",".join(JUDGES))
    j.add_argument("--oracle", default="opus"); j.add_argument("--gate", default="dsflash")
    j.add_argument("--limit", type=int, default=0); j.add_argument("--workers", type=int, default=16)
    pf = sub.add_parser("preflight")
    pf.add_argument("--stage", choices=("author", "validate", "canary", "pilot", "main"), default="author")
    pf.add_argument("--workers", type=int, default=8)
    pf.add_argument("--mode", choices=("batch", "live"), default="batch")
    pf.add_argument("--quality-check", action="store_true")
    args = ap.parse_args()
    if args.cmd == "validation-plan":
        cmd_validation_plan(args)
        return
    if args.cmd == "qualify-validate":
        preflight.require("validate", RUN_ROOT, V.QUALIFICATION_WORKERS, args.mode, qualification=True)
        cmd_qualify_validate(args)
        return
    if args.cmd == "preflight":
        report = preflight.check(args.stage, RUN_ROOT, args.workers, args.mode, args.quality_check)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["ready"] else 2)
    if args.cmd != "split":
        stage = args.split if args.cmd == "judge" else args.cmd
        workers = args.workers if args.cmd in ("judge", "validate") else (8 if args.cmd == "author" else 16)
        mode = getattr(args, "mode", "live")
        quality = getattr(args, "quality_check", False)
        if args.cmd == "author":
            if args.attempts != 2 and not quality:
                raise ValueError("a single authoring attempt is only available for a bounded quality check")
            if args.spend_cap is not None and (not quality or not 0 < args.spend_cap <= STAGE_CAPS["author"]):
                raise ValueError("a quality-check spend cap must be positive and cannot exceed the author-stage cap")
        if quality:
            worlds = A.world_ids(args.worlds)
            if args.only:
                worlds = [w for w in worlds if w[0] in args.only.split(",")]
            if not 1 <= len(worlds) <= 8:
                raise ValueError("quality check must request between one and eight worlds")
        preflight.require(stage, RUN_ROOT, workers, mode, quality)
        if stage == "author" and not quality:
            from .expansion import require_author
            require_author(RUN_ROOT, args)
    {"author": cmd_author, "validate": cmd_validate, "split": cmd_split, "canary": cmd_canary, "judge": cmd_judge}[args.cmd](args)


if __name__ == "__main__":
    main()
