"""Judge arms, the query loop with gate and oracle, and the top2 packet arm."""
from __future__ import annotations

import hashlib
import re

from analysis.infra.parsing import normalize_oracle, parse_verdict_strict
from rejudge.composer import clean_extract_claim
from rejudge.oracle_channel import is_done_robust
from rejudge.query_screen import screen_query

from . import prompts_study as P
from .debate import render_transcript
from .providers import Request

ARMS = ("world_alone", "debate_k0", "debate_k1", "debate_k2", "debate_k6", "debate_world", "top2")
ORDERS = ("key_a", "key_b")

# Output limits include reasoning tokens. Revised from pilot measurements, frozen before main.
JUDGE_MAX_TOKENS = {
    "luna": 16000, "terra": 16000, "sol": 16000,
    "haiku": 2048, "sonnet": 16000, "opus": 16000,
    "llama70": 2048, "qwen38": 16384,
}
ORACLE_MAX_TOKENS = 4000
GATE_MAX_TOKENS = 2000


def _h(*parts: str, n: int = 16) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:n]


def norm_claim(c: str) -> str:
    return re.sub(r"\s+", " ", c.strip().rstrip(".").strip()).lower()


def oracle_request(oracle: str, world_id: str, world: str, claim: str) -> Request:
    return Request(f"oracle:{oracle}:{_h(P.ORACLE_SYSTEM, n=8)}:{world_id}:{_h(norm_claim(claim))}", oracle, P.ORACLE_SYSTEM,
                   (("user", P.ORACLE_USER.format(world=world, claim=claim.strip())),), ORACLE_MAX_TOKENS)


def gate_request(gate: str, q: dict, claim: str) -> Request:
    c1, c2 = sorted([q["correct_answer"], q["wrong_answer"]])  # unlabelled, fixed order
    user = P.GATE_USER.format(question=q["question"], cand1=c1, cand2=c2, claim=claim.strip())
    return Request(f"gate:{gate}:{_h(P.GATE_SYSTEM, n=8)}:{q['question_id']}:{_h(norm_claim(claim))}", gate, P.GATE_SYSTEM, (("user", user),), GATE_MAX_TOKENS)


def parse_gate(text: str) -> str:
    t = text.strip().upper()
    a, r = bool(re.search(r"\bADMIT\b", t)), bool(re.search(r"\bREJECT\b", t))
    return "ADMIT" if a and not r else "REJECT"


def budget_of(arm: str) -> int:
    return int(arm.split("_k")[1]) if "_k" in arm else 0


def judge_task(item: dict, judge: str, arm: str, order: str, oracle: str, gate: str, top2_claims: list[str] | None = None):
    """item: question_id, world_id, world_text, question, correct_answer, wrong_answer, turns."""
    key_is_a = order == "key_a"
    pos_a, pos_b = (item["correct_answer"], item["wrong_answer"]) if key_is_a else (item["wrong_answer"], item["correct_answer"])
    base = f"judge:{item['question_id']}:{judge}:{arm}:{order}"
    maxt = JUDGE_MAX_TOKENS.get(judge, 8000)
    rec = {"question_id": item["question_id"], "world_id": item["world_id"], "judge": judge, "arm": arm, "order": order,
           "key_label": "A" if key_is_a else "B", "queries": [], "events": [], "cost": 0.0}

    if arm == "world_alone":
        system, user = P.JUDGE_SYSTEM_WORLD, P.presentation(item["question"], pos_a, pos_b, world=item["world_text"])
    else:
        transcript = render_transcript(item["turns"], key_is_a)
        if arm == "debate_world":
            system = P.JUDGE_SYSTEM_DEBATE_WORLD
            user = P.presentation(item["question"], pos_a, pos_b, transcript=transcript, world=item["world_text"])
        else:
            system = P.JUDGE_SYSTEM_DEBATE
            user = P.presentation(item["question"], pos_a, pos_b, transcript=transcript)

    msgs: list[tuple[str, str]] = []
    k = budget_of(arm)
    if arm == "top2":
        results = []
        for claim in top2_claims or []:
            o = yield oracle_request(oracle, item["world_id"], item["world_text"], claim)
            rec["cost"] += o.cost
            ans = normalize_oracle(o.text) if o.status == "ok" else "INVALID"
            rec["queries"].append({"claim": claim, "oracle": ans, "source": "selector"})
            results.append(f"{len(results) + 1}. CLAIM: {claim}\n   Oracle result: {_shown(ans)}")
        block = "VERIFICATION RESULTS (claims checked against the world document):\n" + ("\n".join(results) if results else "none")
        msgs.append(("user", f"{user}\n\n{block}\n\n{P.VERDICT_BLOCK}"))
    elif k == 0:
        msgs.append(("user", f"{user}\n\n{P.VERDICT_BLOCK}"))
    else:
        msgs.append(("user", f"{user}\n\n{P.QUERY_PROMPT.format(remaining=k, total=k)}"))
        used, step, rejected_once = 0, 0, False
        while used < k:
            r = yield Request(f"{base}:s{step}", judge, system, tuple(msgs), maxt)
            step += 1
            rec["cost"] += r.cost
            if r.status != "ok" or not r.text.strip():
                rec["events"].append({"step": step, "event": "query_step_failure", "status": r.status, "detail": r.detail[:300]})
                if r.text.strip():
                    msgs.append(("assistant", r.text))
                else:
                    msgs.append(("assistant", "DONE"))
                break
            msgs.append(("assistant", r.text))
            if is_done_robust(r.text):
                rec["events"].append({"step": step, "event": "done", "used": used})
                break
            claim, well_formed = clean_extract_claim(r.text)
            screen = screen_query(claim, item["correct_answer"], item["wrong_answer"])
            decision, gate_reason = ("ADMIT", "") if screen.allowed else ("REJECT", ",".join(screen.reasons))
            if screen.allowed:
                g = yield gate_request(gate, item, claim)
                rec["cost"] += g.cost
                decision = parse_gate(g.text) if g.status == "ok" else "REJECT"
                gate_reason = "classifier" if decision == "REJECT" else ""
            qrec = {"slot": used + 1, "raw": r.text[:2000], "claim": claim, "well_formed": well_formed,
                    "screen": list(screen.reasons), "gate": decision, "retry": rejected_once}
            if decision == "REJECT":
                qrec["gate_reason"] = gate_reason
                rec["queries"].append(qrec)
                if not rejected_once:
                    rejected_once = True
                    msgs.append(("user", f"{P.REJECTION}\n\n{P.QUERY_PROMPT.format(remaining=k - used, total=k)}"))
                    continue
                used += 1
                rejected_once = False
                note = f"{P.REJECTION}. This verification slot is used up."
                if used < k:
                    msgs.append(("user", f"{note}\n\n{P.QUERY_PROMPT.format(remaining=k - used, total=k)}"))
                else:
                    msgs.append(("user", f"{note}\n\n{P.VERDICT_BLOCK}"))
                continue
            o = yield oracle_request(oracle, item["world_id"], item["world_text"], claim)
            rec["cost"] += o.cost
            ans = normalize_oracle(o.text) if o.status == "ok" else "INVALID"
            qrec["oracle"] = ans
            rec["queries"].append(qrec)
            used += 1
            rejected_once = False
            nxt = P.QUERY_PROMPT.format(remaining=k - used, total=k) if used < k else P.VERDICT_BLOCK
            msgs.append(("user", f"Oracle result: {_shown(ans)}\n\n{nxt}"))
        else:
            pass
        if msgs[-1][0] == "assistant":  # stopped early (DONE or failure): ask for the verdict
            msgs.append(("user", P.VERDICT_BLOCK))
    v = yield Request(f"{base}:verdict", judge, system, tuple(msgs), maxt)
    rec["cost"] += v.cost
    parsed = parse_verdict_strict(v.text) if v.status == "ok" else {"verdict": None, "confidence": None, "reasoning": "", "parse_ok": False}
    rec.update({"verdict_status": v.status, "verdict": parsed["verdict"], "confidence": parsed["confidence"],
                "parse_ok": parsed["parse_ok"], "reasoning": (parsed.get("reasoning") or "")[:4000],
                "correct": parsed["verdict"] == rec["key_label"], "n_queries_used": sum(1 for x in rec["queries"] if "oracle" in x),
                "input_tokens": v.input_tokens, "output_tokens": v.output_tokens})
    return rec


def _shown(ans: str) -> str:
    return "UNAVAILABLE" if ans == "INVALID" else ans


def selector_request(item: dict, n: int = 2) -> Request:
    transcript = render_transcript(item["turns"], True)
    pres = P.presentation(item["question"], item["correct_answer"], item["wrong_answer"], transcript=transcript)
    return Request(f"select:{item['question_id']}:fable:n{n}", "fable", P.SELECTOR_SYSTEM,
                   (("user", P.SELECTOR_USER.format(presentation=pres, n=n)),), 16000)


def parse_selector(text: str, n: int = 2) -> list[str]:
    claims = [m.group(1).strip() for m in re.finditer(r"^\s*(?:\d+[.)]\s*)?CLAIM:\s*(.+)$", text, re.M)]
    return claims[:n]
