"""Blind debate-transcript generator for the phase-2 calibration.

Port source: `debate.py` (repo root, READ-ONLY pilot reference). This module reproduces the
pilot's turn structure (3 rounds, 2 slots/round, one debater turn per slot) but is
deliberately NOT a line-for-line port: three things changed.

1. Round 1 is blind for BOTH sides, not just the structurally-first speaker.
   The pilot builds each turn's `debate_history` from whatever is already in the
   accumulated `turns` list at call time. Because turns are generated sequentially
   (slot 0 then slot 1, in the same round), the round-1 slot-1 speaker's history already
   contained slot 0's just-generated turn -- i.e. the "second" debater in round 1 could see
   and rebut the "first" debater's opening argument, even though round 1 is supposed to be
   simultaneous opening statements. Separately, and more consequentially: the pilot's
   `user_prompt_template` (in `experiment_protocol.json`, `honest_debater` /
   `dishonest_debater`) has an unconditional `OPPONENT'S POSITION: {..._answer}` field that
   is filled in on EVERY round, including round 1, before either side has said anything.
   That told each debater exactly which claim the opponent would defend before either turn
   was generated, so even genuine "opening" arguments came out shaped as rebuttals
   ("saw the opponent's upcoming case in advance" per the phase-2 design doc).

   Fix (see `build_turn_prompt` / `strip_opponent_position`): for round_idx == 0,
   `debate_history` is forced empty regardless of what `turns` holds, AND the
   "OPPONENT'S POSITION: {...}" line is stripped out of the round-1 user prompt for both
   roles. In round 1 each debater sees only the world document, the question, its own
   assigned answer, and its role instructions (for the dishonest role, that includes its own
   `wrong_answer_defensibility` strategy notes -- those describe how to defend the debater's
   OWN position and never reference the opponent, so they are not opponent material and are
   kept in every round, matching the pilot). Rounds 2-3 use the pilot's template and
   history-building unchanged: by then both sides have genuinely spoken, so
   `debate_history` legitimately contains "both sides' prior turns, never anything
   unspoken", and the OPPONENT'S POSITION field is restored (it was never hidden
   information by round 2 anyway -- the opponent's actual argument text is right there in
   the transcript).

2. Counterbalanced opening speaker via `counterbalance_assignment`, not the pilot's
   `random.Random(make_seed(question_id, transcript_index)).choice([True, False])` per pair.
   An independent per-pair coin flip does not guarantee an exact 50/50 split -- empirically,
   over the actual 24-question x 2-transcript calibration set (48 pairs) it lands on 26/48
   or 23/48 depending on the exact scheme, not the required 24/48. Instead every
   (question_id, transcript_index) pair is ranked by its `config.make_seed` value and the
   lower half is assigned honest-first; this is still fully determined by
   (question_id, transcript_index) and nothing else, still deterministic and reproducible,
   but guarantees an exact half/half split by construction.

3. Word-cap protocol (`generate_turn`): `capped3` appends a 150-word instruction to the
   debater prompt and mechanically validates the response (whitespace-split word count). Up
   to 3 generation attempts per turn; an over-limit attempt is regenerated with a stronger
   reminder appended; every over-limit attempt is logged on the turn record (attempt index
   and response length only, never the response text); if all 3 attempts are over the limit
   the final attempt is kept and `word_cap_violated` is set true.

Everything else (system prompts, user prompt templates, per-round-2/3 history formatting,
turn ordering) is ported as-is from `experiment_protocol.json` and `debate.py`.

One structural difference from the pilot that is NOT a bug fix, just a consequence of
reusing existing rejudge infrastructure: `rejudge.api_client.RejudgeClient.complete` is
synchronous (unlike the pilot's `api.complete`), so this module and its call sites are
synchronous throughout -- no asyncio.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import records
from rejudge.api_client import CapExceededError, RejudgeClient
from rejudge.config import load_protocol, make_seed
from rejudge.runner import _world_documents, load_done_keys

DEFAULT_WORD_CAP = 150
MAX_CAP_ATTEMPTS = 3
TURN_MAX_TOKENS = 512
CANARY_MAX_TOKENS = 64
TURN_SEED_STRIDE = 10   # headroom per turn so cap-retry seeds never collide with the next turn

PROTOCOL_WORD_CAPS = {"uncapped3": None, "capped3": DEFAULT_WORD_CAP}

CAP_INSTRUCTION = f"Your response must be {DEFAULT_WORD_CAP} words or fewer."
STRONGER_REMINDER = (
    "REMINDER: your previous response exceeded the word limit. You MUST keep this response "
    f"to {DEFAULT_WORD_CAP} words or fewer this time. Cut it down.")

_OPPONENT_ANCHORS = {
    True: "OPPONENT'S POSITION: {wrong_answer}\n\n",
    False: "OPPONENT'S POSITION: {correct_answer}\n\n",
}

CANARY_PROMPTS = [
    "In exactly 20 words, describe what a mountain range is.",
    "In exactly 20 words, explain what causes rain.",
    "In exactly 20 words, describe the function of a treaty between two nations.",
    "In exactly 20 words, explain what a trade tariff is.",
    "In exactly 20 words, describe what a debate judge does.",
]


def word_count(text: str) -> int:
    return len(text.split())


def strip_opponent_position(template: str, is_honest: bool) -> str:
    """Remove the round-1 opponent-leak: the pilot's unconditional
    "OPPONENT'S POSITION: {...}" line. Raises ValueError if the anchor line is missing
    (protocol drift guard, matching rejudge.config.clean_query_phase_prompt).
    """
    anchor = _OPPONENT_ANCHORS[is_honest]
    if anchor not in template:
        raise ValueError(
            "pilot debater template opponent-position anchor not found -- protocol drifted? "
            "cannot safely build a blind round-1 prompt")
    return template.replace(anchor, "", 1)


def _debate_history_text(turns: list[dict], honest_first: bool) -> str:
    """Port of debate.py:_format_debate_history for dict-shaped turns (speaker/text keys,
    matching the rest of rejudge, which works with raw dicts rather than pydantic models).
    Unchanged from the pilot: each TURN gets its own "Round N" label (not each round-pair);
    that labeling quirk is cosmetic and out of scope for the fixes above.
    """
    if not turns:
        return ""
    lines = ["DEBATE HISTORY:"]
    for i, turn in enumerate(turns):
        first_slot = i % 2 == 0
        if honest_first:
            label = "Debater A" if first_slot else "Debater B"
        else:
            label = "Debater B" if first_slot else "Debater A"
        lines.append(f"Round {i + 1}:\n{label}: {turn['text']}")
    return "\n\n".join(lines)


def build_turn_prompt(*, round_idx: int, is_honest: bool, question: dict, world_document: str,
                      turns: list[dict], honest_first: bool, protocol: dict,
                      cap_words: int | None) -> tuple[str, str]:
    """Build (system, user) for one debater turn. `turns` is the transcript-so-far
    accumulator; for round_idx == 0 it is IGNORED (round 1 is always blind), regardless of
    what it holds -- this is the core of the round-1-blindness fix.
    """
    tmpl = protocol["honest_debater"] if is_honest else protocol["dishonest_debater"]
    system = tmpl["system_prompt"]
    user_template = tmpl["user_prompt_template"]

    if round_idx == 0:
        user_template = strip_opponent_position(user_template, is_honest)
        history_str = ""
    else:
        history_str = _debate_history_text(turns, honest_first)

    fmt = dict(world_document=world_document, question=question["question"],
              correct_answer=question["correct_answer"], wrong_answer=question["wrong_answer"],
              debate_history=history_str)
    if not is_honest:
        fmt["wrong_answer_defensibility"] = question.get("wrong_answer_defensibility", "")

    user = user_template.format(**fmt)
    if cap_words is not None:
        user = f"{user}\n\n{CAP_INSTRUCTION}"
    return system, user


def counterbalance_assignment(pairs: list[tuple]) -> dict:
    """Deterministically assign honest_first for each (question_id, transcript_index) pair,
    guaranteeing an exact half/half split (see module docstring point 2). Pure function of
    `pairs`: same input always produces the same output.
    """
    unique = sorted(set(pairs), key=lambda p: (make_seed(*p), p[0], p[1]))
    half = len(unique) // 2
    honest = set(unique[:half])
    return {p: (p in honest) for p in pairs}


def generate_turn(client, *, model: str, temperature: float, seed: int, system: str, user: str,
                  cap_words: int | None, kind: str = "query") -> tuple[str, dict | None]:
    """Generate one debater turn. If `cap_words` is None, a single call is made and no
    metadata is returned. If `cap_words` is set, apply the mechanical word-cap validation
    protocol: up to MAX_CAP_ATTEMPTS attempts, a stronger reminder appended on retries, every
    over-limit attempt logged (attempt index + response length, never the text).
    """
    if cap_words is None:
        text = client.complete([{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                               model, temperature, seed, TURN_MAX_TOKENS, kind=kind)
        return text, None

    over_limit_attempts: list[dict] = []
    text = ""
    for attempt in range(1, MAX_CAP_ATTEMPTS + 1):
        attempt_user = user if attempt == 1 else f"{user}\n\n{STRONGER_REMINDER}"
        text = client.complete([{"role": "system", "content": system},
                                {"role": "user", "content": attempt_user}],
                               model, temperature, seed + attempt - 1, TURN_MAX_TOKENS, kind=kind)
        if word_count(text) <= cap_words:
            return text, {"word_cap_violated": False, "regen_attempts": len(over_limit_attempts),
                          "over_limit_attempts": over_limit_attempts}
        over_limit_attempts.append({"attempt": attempt, "length": len(text)})

    return text, {"word_cap_violated": True, "regen_attempts": len(over_limit_attempts),
                  "over_limit_attempts": over_limit_attempts}


def generate_transcript(question: dict, world_document: str, transcript_index: int,
                        honest_first: bool, protocol: dict, client, *, debater_model: str,
                        protocol_name: str) -> dict:
    if protocol_name not in PROTOCOL_WORD_CAPS:
        raise ValueError(f"unknown protocol: {protocol_name!r}")
    cap_words = PROTOCOL_WORD_CAPS[protocol_name]
    n_rounds = protocol["protocol"]["debate_phase"]["n_rounds"]
    temperature = protocol["protocol"]["temperature"]["debater"]
    base_seed = make_seed(question["id"], transcript_index)

    turns: list[dict] = []
    for round_idx in range(n_rounds):
        for slot in range(2):
            is_honest = (slot == 0) == honest_first
            system, user = build_turn_prompt(
                round_idx=round_idx, is_honest=is_honest, question=question,
                world_document=world_document, turns=turns, honest_first=honest_first,
                protocol=protocol, cap_words=cap_words)
            turn_seed = base_seed + (round_idx * 2 + slot) * TURN_SEED_STRIDE
            text, cap_meta = generate_turn(
                client, model=debater_model, temperature=temperature, seed=turn_seed,
                system=system, user=user, cap_words=cap_words, kind="query")
            turn = {"speaker": "honest" if is_honest else "dishonest", "text": text,
                   "round": round_idx + 1}
            if cap_meta is not None:
                turn.update(cap_meta)
            turns.append(turn)

    return {
        "question_id": question["id"],
        "transcript_index": transcript_index,
        "world": question["world"],
        "question": question["question"],
        "correct_answer": question["correct_answer"],
        "wrong_answer": question["wrong_answer"],
        "honest_first": honest_first,
        "debate_transcript": turns,
        "debater_model": debater_model,
        "protocol": protocol_name,
        "temperature": temperature,
        "seed": base_seed,
        "harness_version": records.get_git_sha(),
        "created_at": records.utc_now_iso(),
        "dry_run": getattr(client, "dry_run", False),
        "cell_key": f"{protocol_name}|{debater_model}|{question['id']}|{transcript_index}",
    }


def run_canary_checks(client, model_id: str, protocol: dict) -> list[dict]:
    """Run CANARY_PROMPTS as tiny real completions and report per-prompt outcome. Pure with
    respect to I/O other than `client.complete` -- callers decide how/whether to print.
    """
    temperature = protocol["protocol"]["temperature"]["debater"]
    results = []
    for i, prompt in enumerate(CANARY_PROMPTS):
        entry: dict[str, object] = {"index": i, "prompt": prompt}
        start = time.monotonic()
        before = getattr(client, "total_tokens", 0)
        try:
            text = client.complete([{"role": "user", "content": prompt}], model_id, temperature,
                                   1000 + i, CANARY_MAX_TOKENS, kind="query")
        except Exception as exc:
            entry.update(ok=False, latency_s=time.monotonic() - start, error=str(exc))
        else:
            after = getattr(client, "total_tokens", 0)
            entry.update(ok=True, latency_s=time.monotonic() - start, preview=text[:40],
                        tokens_used=after - before)
        results.append(entry)
    return results


def _load_question_bank() -> dict:
    bank = {}
    for f in Path("questions").glob("*.json"):
        for q in json.loads(f.read_text(encoding="utf-8")):
            bank[q["id"]] = q
    return bank


def _parse_debater_models(values, protocol: dict) -> list[str]:
    if not values:
        return [protocol["protocol"]["models"]["debater"]]
    models: list[str] = []
    for v in values:
        for m in v.split(","):
            m = m.strip()
            if m and m not in models:
                models.append(m)
    return models


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="rejudge/output/calibration_questions.json")
    ap.add_argument("--transcripts-per-question", type=int, default=2)
    ap.add_argument("--protocols", default="capped3,uncapped3")
    ap.add_argument("--debater-model", action="append", default=None)
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="rejudge/output/calibration_transcripts.jsonl")
    ap.add_argument("--canary", default=None)
    args = ap.parse_args(argv)

    protocol = load_protocol()

    if args.canary:
        if args.dry_run:
            print("REFUSED: --canary always makes live calls; do not combine with --dry-run.",
                  file=sys.stderr)
            return 2
        if args.approved_cap is None:
            print("REFUSED: --canary requires --approved-cap USD (it makes live calls).",
                  file=sys.stderr)
            return 2
        out_dir = Path(args.out).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        client = RejudgeClient(approved_cap_usd=args.approved_cap, dry_run=False,
                               error_log_path=str(out_dir / "debate_gen_canary_errors.jsonl"))
        results = run_canary_checks(client, args.canary, protocol)
        ok = True
        for r in results:
            if r["ok"]:
                print(f"canary[{r['index']}] OK {r['latency_s']:.2f}s "
                      f"tokens={r['tokens_used']} :: {r['preview']!r}")
            else:
                ok = False
                print(f"canary[{r['index']}] FAIL {r['latency_s']:.2f}s :: {r['error']}",
                      file=sys.stderr)
        return 0 if ok else 1

    if args.transcripts_per_question <= 0:
        print("REFUSED: --transcripts-per-question must be a positive integer ('run nothing' "
              "vs 'no limit' is ambiguous); omit for the default or pass a positive value.",
              file=sys.stderr)
        return 2

    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD (spend policy).", file=sys.stderr)
        return 2

    protocol_names = [p.strip() for p in args.protocols.split(",") if p.strip()]
    unknown = [p for p in protocol_names if p not in PROTOCOL_WORD_CAPS]
    if unknown:
        print(f"unknown protocols: {unknown}", file=sys.stderr)
        return 2

    debater_models = _parse_debater_models(args.debater_model, protocol)

    question_ids = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    bank = _load_question_bank()
    missing = [qid for qid in question_ids if qid not in bank]
    if missing:
        print(f"unknown question ids (not found under questions/*.json): {missing}",
              file=sys.stderr)
        return 2
    questions = [bank[qid] for qid in question_ids]
    worlds = _world_documents()

    pairs = [(q["id"], t) for q in questions for t in range(args.transcripts_per_question)]
    honest_first_map = counterbalance_assignment(pairs)

    jobs = []
    for protocol_name in protocol_names:
        for debater_model in debater_models:
            for q in questions:
                for t in range(args.transcripts_per_question):
                    key = f"{protocol_name}|{debater_model}|{q['id']}|{t}"
                    jobs.append({"protocol_name": protocol_name, "debater_model": debater_model,
                                "question": q, "transcript_index": t,
                                "honest_first": honest_first_map[(q["id"], t)], "cell_key": key})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path = out_path.parent / "debate_gen_failed.jsonl"
    done = load_done_keys(out_path)
    todo = [j for j in jobs if j["cell_key"] not in done]
    print(f"{len(jobs)} transcripts, {len(done)} done, {len(todo)} to run "
          f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

    client = RejudgeClient(approved_cap_usd=args.approved_cap or 0.0, dry_run=args.dry_run,
                           error_log_path=str(out_path.parent / "debate_gen_errors.jsonl"))
    lock = threading.Lock()
    cap_hit = threading.Event()

    def run_job(job):
        if cap_hit.is_set():
            return
        try:
            rec = generate_transcript(
                job["question"], worlds[job["question"]["world"]], job["transcript_index"],
                job["honest_first"], protocol, client, debater_model=job["debater_model"],
                protocol_name=job["protocol_name"])
        except CapExceededError:
            cap_hit.set()
            return
        except Exception as exc:
            with lock:
                with open(failed_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"cell_key": job["cell_key"], "error": str(exc),
                                        "ts": records.utc_now_iso()}) + "\n")
            print(f"WARN: {job['cell_key']} failed: {exc}", file=sys.stderr)
            return
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run_job, todo))

    if cap_hit.is_set():
        print("CAP REACHED -- run halted; resume after raising cap.", file=sys.stderr)
        return 3

    print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
