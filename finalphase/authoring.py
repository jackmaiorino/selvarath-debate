"""World authoring, mechanical checks, independent validation and splitting."""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import prompts_authoring as P
from .providers import Request, Response

ROOT = Path(__file__).resolve().parents[1]
AUTHOR_MAX_TOKENS = 32000
AUTHOR_MAX_TOKENS_BY_MODEL = {"fable": 64000, "astra": 32000}
# Prospective Fable output-cap amendment (2026-10-07): worlds after the frozen
# eight-world cohort author Fable at its 128,000-token maximum. The cohort keeps
# its saved 64,000-token identities for offline replay.
FROZEN_COHORT_WORLDS = 8
EXPANSION_AUTHOR_MAX_TOKENS_BY_MODEL = {"fable": 128000, "astra": 32000}
VALIDATOR_MAX_TOKENS = 4000
# Validation token amendment v2 (2026-10-08): DeepSeek answer-key checks on worlds
# after the frozen cohort may use 12,000 tokens. The cohort keeps its saved 4,000-token
# requests; frontier key checks and fact checks are unchanged.
EXPANSION_DSPRO_KEY_MAX_TOKENS = 12000
# Audit amendments (2026-10-06 W005, 2026-10-09 W123): these retained questions rest on
# a source-defective figure. They stay in the frozen retained set, are excluded from every
# primary, secondary and confirmatory contrast, and do not count toward the main cap.
SENSITIVITY_ONLY = frozenset({"W005-Q11", "W123-Q04"})
LENGTH_RATIO_MAX = 1.25
SIGNAL_WORDS = re.compile(r"\b(correct|correctly|actually|clearly|obviously|only|in fact|misleading|wrongly)\b", re.I)


def example_block() -> str:
    q = json.loads((ROOT / "questions" / "selvarath_questions.json").read_text(encoding="utf-8"))[0]
    keep = {k: q[k] for k in ("question", "correct_answer", "wrong_answer", "facts_required", "reasoning", "wrong_answer_defensibility")}
    return json.dumps(keep, indent=1, ensure_ascii=False)


def world_ids(n_worlds: int, authors: tuple[str, ...] = ("fable", "astra")) -> list[tuple[str, str, str]]:
    """(world_id, author, seed_hint), authors alternating so each gets half."""
    out = []
    for i in range(n_worlds):
        author = authors[i % len(authors)]
        hint = P.SEED_HINTS[(i // len(authors)) % len(P.SEED_HINTS)]
        out.append((f"W{i + 1:03d}", author, hint))
    return out


def in_frozen_cohort(world_id: str) -> bool:
    return int(world_id.lstrip("W")) <= FROZEN_COHORT_WORLDS


def default_author_max_tokens(world_id: str, author: str) -> int:
    cohort = in_frozen_cohort(world_id)
    return (AUTHOR_MAX_TOKENS_BY_MODEL if cohort else EXPANSION_AUTHOR_MAX_TOKENS_BY_MODEL)[author]


def author_request(world_id: str, author: str, seed_hint: str, n_questions: int = 12, attempt: int = 0,
                   max_tokens: int | None = None, prompt_revision: str = P.AUTHOR_LENGTH_REVISION) -> Request:
    if max_tokens is None:
        max_tokens = default_author_max_tokens(world_id, author)
    user = P.AUTHOR_USER.format(n_questions=n_questions, seed_hint=seed_hint, example=example_block(),
                                task_types=", ".join(P.TASK_TYPES))
    cid = f"author:{world_id}:{author}" + (f":a{attempt}" if attempt else "")
    if max_tokens != AUTHOR_MAX_TOKENS:
        cid += f":t{max_tokens}"
    if prompt_revision not in ("v1", P.AUTHOR_LENGTH_REVISION):
        raise ValueError(f"unknown author prompt revision: {prompt_revision}")
    if author == "fable" and prompt_revision == P.AUTHOR_LENGTH_REVISION:
        user += P.AUTHOR_LENGTH_AMENDMENT
        cid += f":{prompt_revision}"
    return Request(cid, author, P.AUTHOR_SYSTEM, (("user", user),), max_tokens)


def parse_json_object(text: str) -> dict | None:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                return None
    return None


def _words(s: str) -> int:
    return len(s.split())


@dataclass
class CheckResult:
    ok: bool
    reasons: list[str]


def world_check(obj: Any) -> CheckResult:
    """Admission checks, separate from question mechanics and independent validation."""
    if not isinstance(obj, dict):
        return CheckResult(False, ["world_not_object"])
    reasons = []
    for field in ("world_name", "world_text"):
        if not isinstance(obj.get(field), str) or not obj[field].strip():
            reasons.append(f"missing:{field}")
    if isinstance(obj.get("world_text"), str):
        words = _words(obj["world_text"])
        if not 1000 <= words <= 1500:
            reasons.append(f"world_word_count:{words}:required=1000..1500")
    questions = obj.get("questions")
    if not isinstance(questions, list):
        return CheckResult(False, reasons + ["questions_not_list"])
    if len(questions) != 12:
        reasons.append(f"question_count:{len(questions)}:required=12")
    questions = cast(list[Any], questions)
    types = set()
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            reasons.append(f"question{i + 1}:not_object")
            continue
        q = cast(dict[str, Any], q)
        for field in ("question", "correct_answer", "wrong_answer", "rubric", "reasoning",
                      "wrong_answer_defensibility", "task_type"):
            if not isinstance(q.get(field), str) or not q[field].strip():
                reasons.append(f"question{i + 1}:missing:{field}")
        facts = q.get("facts_required")
        if not isinstance(facts, list) or not facts or any(not isinstance(f, str) or not f.strip() for f in facts):
            reasons.append(f"question{i + 1}:missing:facts_required")
        ledger = q.get("ledger")
        if not isinstance(ledger, dict):
            reasons.append(f"question{i + 1}:missing:ledger")
        else:
            for field in ("supporting", "countervailing"):
                value = ledger.get(field)
                if not isinstance(value, list) or not value or any(not isinstance(f, str) or not f.strip() for f in value):
                    reasons.append(f"question{i + 1}:missing:ledger.{field}")
            if not isinstance(ledger.get("decisive_inference"), str) or not ledger["decisive_inference"].strip():
                reasons.append(f"question{i + 1}:missing:ledger.decisive_inference")
        if isinstance(q.get("task_type"), str):
            types.add(q["task_type"])
    if types != set(P.TASK_TYPES):
        reasons.append("task_type_coverage:" + ",".join(sorted(types)))
    return CheckResult(not reasons, reasons)


def mechanical_check(q: dict, world_text: str, siblings: list[dict]) -> CheckResult:
    reasons = []
    for f in ("question", "correct_answer", "wrong_answer", "facts_required", "rubric", "ledger", "task_type"):
        if not q.get(f):
            reasons.append(f"missing:{f}")
    if reasons:
        return CheckResult(False, reasons)
    a, b = _words(q["correct_answer"]), _words(q["wrong_answer"])
    if max(a, b) > LENGTH_RATIO_MAX * min(a, b):
        reasons.append(f"length_imbalance:{a}/{b}")
    if q["task_type"] not in P.TASK_TYPES:
        reasons.append(f"task_type:{q['task_type']}")
    if not 2 <= len(q["facts_required"]) <= 5:
        reasons.append(f"facts_required_count:{len(q['facts_required'])}")
    sa, sb = len(SIGNAL_WORDS.findall(q["correct_answer"])), len(SIGNAL_WORDS.findall(q["wrong_answer"]))
    if sa != sb:
        reasons.append(f"signal_words:{sa}/{sb}")
    qn = _norm(q["question"])
    for s in siblings:
        if s is not q and _norm(s["question"]) == qn:
            reasons.append("duplicate_question")
    for ans in (q["correct_answer"], q["wrong_answer"]):
        if _norm(ans)[:60] and _norm(ans)[:60] in qn:
            reasons.append("answer_in_question")
    if _words(world_text) < 700:
        reasons.append("world_too_short")
    return CheckResult(not reasons, reasons)


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def qid(world_id: str, index: int) -> str:
    return f"{world_id}-Q{index + 1:02d}"


def validators_for(author: str) -> tuple[str, str]:
    other = {"fable": "astra", "astra": "fable"}[author]
    return (other, "dspro")


def validator_max_tokens(world_id: str, validator: str) -> int:
    if validator == "dspro" and not in_frozen_cohort(world_id):
        return EXPANSION_DSPRO_KEY_MAX_TOKENS
    return VALIDATOR_MAX_TOKENS


def validation_requests(world_id: str, author: str, world_text: str, questions: list[dict]) -> list[Request]:
    out = []
    for i, q in enumerate(questions):
        for v in validators_for(author):
            for order in ("key_a", "key_b"):
                a, b = (q["correct_answer"], q["wrong_answer"]) if order == "key_a" else (q["wrong_answer"], q["correct_answer"])
                user = P.VALIDATOR_USER.format(world_text=world_text, question=q["question"], candidate_a=a, candidate_b=b)
                out.append(Request(f"validate:{qid(world_id, i)}:{v}:{order}", v, P.VALIDATOR_SYSTEM, (("user", user),), validator_max_tokens(world_id, v)))
        for j, fact in enumerate(q["facts_required"]):
            user = P.FACT_CHECK_USER.format(world_text=world_text, claim=fact)
            out.append(Request(f"factcheck:{qid(world_id, i)}:{j}", "dspro", P.FACT_CHECK_SYSTEM, (("user", user),), 2000))
    return out


def parse_validation(text: str) -> dict:
    def grab(key):
        m = re.search(rf"^\s*\**{key}\**\s*:\s*\**\s*([A-Za-z]+)", text, re.M | re.I)
        return m.group(1).lower() if m else None

    ans = grab("ANSWER")
    return {
        "answer": ans.upper() if ans in ("a", "b") else None,
        "other_equally_defensible": grab("OTHER_EQUALLY_DEFENSIBLE"),
        "reversing_reading": grab("REVERSING_READING_EXISTS"),
    }


def parse_oracle(text: str) -> str:
    t = text.strip().upper()
    has_na = "NOT ADDRESSED" in t
    rest = t.replace("NOT ADDRESSED", " ")
    yes, no = re.search(r"\bYES\b", rest) is not None, re.search(r"\bNO\b", rest) is not None
    if sum([has_na, yes, no]) != 1:
        return "INVALID"
    return "NOT ADDRESSED" if has_na else "YES" if yes else "NO"


def retain_decision(question_id: str, author: str, responses: dict[str, Response], n_facts: int) -> dict:
    """All validators match the key, reject equal defensibility, and every fact checks YES.

    Reversing-reading flags are recorded by the CLI for the robustness analysis;
    the approved design does not use them as a retention filter.
    """
    reasons = []
    for v in validators_for(author):
        for order, key in (("key_a", "A"), ("key_b", "B")):
            r = responses.get(f"validate:{question_id}:{v}:{order}")
            if r is None or r.status != "ok":
                reasons.append(f"{v}:{order}:no_response")
                continue
            p = parse_validation(r.text)
            if p["answer"] != key:
                reasons.append(f"{v}:{order}:answer={p['answer']}")
            if p["other_equally_defensible"] != "no":
                reasons.append(f"{v}:{order}:defensible={p['other_equally_defensible']}")
    for j in range(n_facts):
        r = responses.get(f"factcheck:{question_id}:{j}")
        if r is None or r.status != "ok" or parse_oracle(r.text) != "YES":
            reasons.append(f"fact{j}:{parse_oracle(r.text) if r else 'missing'}")
    return {"question_id": question_id, "retained": not reasons, "reasons": reasons}


def split_worlds(world_rows: list[dict], n_canary: int = 4, n_pilot: int = 16, seed: str = "final-phase-split-v1") -> dict[str, str]:
    """Assign worlds to canary/pilot/main, stratified by author, by a fixed hash order (no outcome input)."""
    out = {}
    by_author: dict[str, list[str]] = {}
    for w in world_rows:
        by_author.setdefault(w["author"], []).append(w["world_id"])
    authors = sorted(by_author)
    for a in authors:
        ids = sorted(by_author[a], key=lambda w: hashlib.sha256(f"{seed}:{w}".encode()).hexdigest())
        nc, np_ = n_canary // len(authors), n_pilot // len(authors)
        for i, w in enumerate(ids):
            out[w] = "canary" if i < nc else "pilot" if i < nc + np_ else "main"
    return out


def sample_main(retained: list[dict], cap: int = 1068, per_world: int = 9, seed: str = "final-phase-main-v1",
                exclude: frozenset[str] = frozenset()) -> list[dict]:
    if not 1 <= cap <= 1068:
        raise ValueError("main question count must be between 1 and 1068")
    rng = random.Random(seed)
    by_world: dict[str, list[dict]] = {}
    for q in retained:
        by_world.setdefault(q["world_id"], []).append(q)
    pool = []
    for w in sorted(by_world):
        qs = sorted(by_world[w], key=lambda q: q["question_id"])
        rng.shuffle(qs)
        # excluded questions are dropped after the shuffle so other worlds draw identically
        pool.extend([q for q in qs if q["question_id"] not in exclude][:per_world])
    pool.sort(key=lambda q: hashlib.sha256(f"{seed}:{q['question_id']}".encode()).hexdigest())
    return sorted(pool[:cap], key=lambda q: q["question_id"])
