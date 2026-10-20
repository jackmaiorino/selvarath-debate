"""Debate generation: 3 rounds, blind first round, honest-first balanced by hash."""
from __future__ import annotations

import hashlib

from . import prompts_study as P
from .providers import Request, Response

N_ROUNDS = 3
DEBATER_MAX_TOKENS = {"fable": 32000, "astra": 32000}
MAX_CAP_ATTEMPTS = 3
MAX_EMPTY_REGEN = 2


def honest_first(question_id: str, salt: str = "final-phase-order-v1") -> bool:
    return int(hashlib.sha256(f"{salt}:{question_id}".encode()).hexdigest(), 16) % 2 == 0


def _history_for(turns: list[dict], role: str) -> str:
    lines = []
    for i, t in enumerate(turns):
        who = "You" if t["role"] == role else "Opponent"
        lines.append(f"Turn {i + 1} ({who}):\n{t['text']}")
    return "\n\n".join(lines)


def _words(s: str) -> int:
    return len(s.split())


def parse_debater(debater: str) -> tuple[str, str | None]:
    """"fable" -> ("fable", None); "fable@low" -> ("fable", "low"): a model key and an effort override."""
    model, _, effort = debater.partition("@")
    return model, effort or None


def pair_label(honest: str, dishonest: str) -> str:
    """Id-space label of an asymmetric pairing, honest side first; it contains no colon."""
    return f"{honest}~{dishonest}"


def debate_task(q: dict, world: str, debater: str, cap: int | None, condition: str,
                dishonest: str | None = None, error_regen: bool = True):
    """Yields debater Requests; returns the transcript record.

    q: question record with question_id, question, correct_answer, wrong_answer,
    wrong_answer_defensibility. condition labels the id space (e.g. "uncapped", "cap150").
    Self-play by default. With dishonest set, debater plays the honest side and dishonest the
    other; either may carry an effort override ("haiku", "fable@low"). Self-play ids are unchanged.
    error_regen=False regenerates only empty turns, not provider errors (the confirmation's retry rule).
    """
    qid = q["question_id"]
    hf = honest_first(qid)
    order = ["honest", "dishonest"] if hf else ["dishonest", "honest"]
    turns: list[dict] = []
    label = debater if dishonest is None else pair_label(debater, dishonest)
    sides = {"honest": parse_debater(debater), "dishonest": parse_debater(dishonest or debater)}
    base = f"debate:{condition}:{label}:{qid}"

    def req(role: str, rnd: int, history: str | None, attempt: int, last_words: int | None) -> Request:
        system = P.debater_system(role, opening=history is None)
        user = P.debater_user(role, world, q, history)
        if cap is not None:
            user += P.CAP_LINE.format(cap=cap)
            if last_words is not None:
                user += P.CAP_RETRY_LINE.format(n=last_words, cap=cap)
        cid = f"{base}:r{rnd}:{role}" + (f":a{attempt}" if attempt else "")
        model, effort = sides[role]
        return Request(cid, model, system, (("user", user),), DEBATER_MAX_TOKENS.get(model, 16000), effort=effort)

    def finish(role: str, rnd: int, history: str | None, first: Response):
        """Handle empty-turn regeneration and cap retries for one turn (generator)."""
        r, attempt, over = first, 0, []
        while True:
            empty = r.status in ("ok", "truncated") and not r.text.strip()
            if ((r.status == "error" and error_regen) or empty) and attempt < MAX_EMPTY_REGEN:
                attempt += 1
                r = yield req(role, rnd, history, attempt, None)
                continue
            if cap is not None and r.status == "ok" and _words(r.text) > cap and attempt < MAX_CAP_ATTEMPTS - 1:
                over.append(_words(r.text))
                attempt += 1
                r = yield req(role, rnd, history, attempt, _words(r.text))
                continue
            return {"role": role, "round": rnd, "text": r.text, "status": r.status, "words": _words(r.text),
                    "attempts": attempt + 1, "over_limit_words": over, "cost": r.cost,
                    "cap_violated": cap is not None and _words(r.text) > cap}

    # round 1: both sides blind, generated together
    first = yield [req(role, 1, None, 0, None) for role in order]
    for role, r0 in zip(order, first):
        turns.append((yield from finish(role, 1, None, r0)))
    for rnd in range(2, N_ROUNDS + 1):
        for role in order:
            hist = _history_for(turns, role)
            r0 = yield req(role, rnd, hist, 0, None)
            turns.append((yield from finish(role, rnd, hist, r0)))
    record = {"question_id": qid, "debater": label, "condition": condition, "cap": cap, "honest_first": hf,
              "turns": turns, "cost": sum(t["cost"] for t in turns)}
    if dishonest is not None:
        record.update(honest_debater=debater, dishonest_debater=dishonest)
    return record


def render_transcript(turns: list[dict], key_is_a: bool) -> str:
    out = []
    for i, t in enumerate(turns):
        honest_label, dishonest_label = ("Debater A", "Debater B") if key_is_a else ("Debater B", "Debater A")
        label = honest_label if t["role"] == "honest" else dishonest_label
        out.append(f"Turn {i + 1} ({label}):\n{t['text']}")
    return "\n\n".join(out)
