"""Live-query checker admission screen for Llama (frozen plan of 2026-08-28).

Selects the frozen 96-probe set (all 6 verified runaway triggers plus a deterministic
judge-balanced complement) from the hash-verified bank, dispatches each probe to the
exact production Llama-checker configuration (frozen prompts, frozen decoding, 16-token
budget, non-streaming), and applies the plan's acceptance rule verbatim: 0 invalid of
96 with every trigger completing, else the owner's approved stop-and-report fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_canary_gate import FROZEN_CONFIG_PATH  # noqa: E402
from rejudge.phase2_query_gate import MalformedCheckerOutput, parse_checker_output  # noqa: E402

PLAN_PATH = REPO_ROOT / "rejudge/phase3_v3_llama_checker_screen_plan_2026-08-28.json"
BANK_PATH = REPO_ROOT / "rejudge/phase3_v3_llama_checker_screen_bank_2026-08-28.json"
USAGE_LOG = REPO_ROOT / "rejudge/output/phase3_v3_llama_checker_screen_usage_2026-08-28.jsonl"
RESULTS_LOG = REPO_ROOT / "rejudge/output/phase3_v3_llama_checker_screen_results_2026-08-28.jsonl"
MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
MAX_TOKENS = 16
CEILING_USD = 0.5
LLAMA_USD_PER_MILLION = 1.04
TRANSPORT_RETRIES = 3
TRANSPORT_BACKOFF_SECONDS = 30
PER_JUDGE_COMPLEMENT = 45


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()


def select_probes(bank: dict) -> list[dict]:
    entries = sorted(bank["entries"], key=lambda entry: entry["probe_id"])
    triggers = [e for e in entries if e["gemma_outcome"] == "runaway_finish_length"]
    completed = [e for e in entries if e["gemma_outcome"] == "completed"]
    selected = list(triggers)
    for judge in sorted({e["originating_judge"] for e in completed}):
        stratum = [e for e in completed if e["originating_judge"] == judge]
        if len(stratum) <= PER_JUDGE_COMPLEMENT:
            selected.extend(stratum)
            continue
        stride = len(stratum) / PER_JUDGE_COMPLEMENT
        selected.extend(stratum[int(i * stride)] for i in range(PER_JUDGE_COMPLEMENT))
    return selected


def main(argv: list[str] | None = None) -> int:
    parser_cli = argparse.ArgumentParser(description=__doc__)
    parser_cli.add_argument("--dry-run", action="store_true")
    args = parser_cli.parse_args(argv)

    config = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))["configuration"]
    temperature = config["decoding"]["temperature"]
    seed = config["decoding"]["seed"]
    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    probes = select_probes(bank)
    trigger_count = sum(
        1 for p in probes if p["gemma_outcome"] == "runaway_finish_length")
    by_judge: dict[str, int] = {}
    for probe in probes:
        by_judge[probe["originating_judge"]] = by_judge.get(
            probe["originating_judge"], 0) + 1
    print(f"selected {len(probes)} probes ({trigger_count} runaway triggers) "
          f"by judge {by_judge}")
    if len(probes) != 96 or trigger_count != 6:
        raise SystemExit("frozen selection drifted from the plan (96 probes, 6 triggers)")
    if args.dry_run:
        print(json.dumps({"verdict": "dry_run", "probes": len(probes)}, indent=1))
        return 0

    from together import Together

    client = Together(timeout=120.0)
    reserved_total = 0.0
    invalid = 0
    dispatched = 0
    trigger_pass = 0
    decisions_by_judge: dict[str, dict[str, int]] = {}
    verdict = "pending"
    for index, probe in enumerate(probes):
        prompt_chars = sum(len(m["content"]) for m in probe["messages"])
        reservation = ((prompt_chars / 3 + MAX_TOKENS)
                       * LLAMA_USD_PER_MILLION) / 1_000_000
        if reserved_total + reservation > CEILING_USD:
            verdict = "ceiling_stop"
            print(f"ceiling stop at probe {index}")
            break
        reserved_total += reservation
        started = _utc_now()
        response = None
        for attempt in range(TRANSPORT_RETRIES):
            try:
                response = client.chat.completions.create(
                    model=MODEL, messages=probe["messages"], max_tokens=MAX_TOKENS,
                    temperature=temperature, seed=seed)
                break
            except Exception as exc:  # noqa: BLE001 - bounded transport retry
                _append(USAGE_LOG, {
                    "ts": _utc_now(), "probe_id": probe["probe_id"],
                    "attempt": attempt, "reserved_usd": reservation,
                    "actual_usd": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                if attempt + 1 < TRANSPORT_RETRIES:
                    time.sleep(TRANSPORT_BACKOFF_SECONDS)
        if response is None:
            verdict = "INCOMPLETE_batch_transport"
            print(f"INCOMPLETE at probe {index}: transport exhausted")
            break
        usage = response.usage
        text = response.choices[0].message.content or ""
        finish = str(response.choices[0].finish_reason)
        actual = ((usage.prompt_tokens + usage.completion_tokens)
                  * LLAMA_USD_PER_MILLION) / 1_000_000
        _append(USAGE_LOG, {
            "ts": started, "probe_id": probe["probe_id"],
            "reserved_usd": reservation, "actual_usd": actual,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "finish_reason": finish})
        dispatched += 1
        empty = not text.strip()
        try:
            decision = parse_checker_output(text)
            parse_valid = True
        except MalformedCheckerOutput:
            decision = None
            parse_valid = False
        is_invalid = empty or not parse_valid or finish != "stop"
        judge_tally = decisions_by_judge.setdefault(
            probe["originating_judge"], {"allow": 0, "reject": 0, "invalid": 0})
        if is_invalid:
            judge_tally["invalid"] += 1
        else:
            judge_tally[str(decision)] = judge_tally.get(str(decision), 0) + 1
        _append(RESULTS_LOG, {
            "ts": started, "probe_id": probe["probe_id"],
            "archive": probe["archive"], "cell_key": probe["cell_key"],
            "slot": probe["slot"], "attempt": probe["attempt"],
            "originating_judge": probe["originating_judge"],
            "is_known_trigger": probe["gemma_outcome"] == "runaway_finish_length",
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "finish_reason": finish, "empty": empty, "parse_valid": parse_valid,
            "invalid": is_invalid,
            "decision": None if is_invalid else str(decision),
            "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        if is_invalid:
            invalid += 1
            verdict = "REJECTED_first_invalid"
            print(f"REJECTED at probe {index} (empty={empty} parse_valid={parse_valid} "
                  f"finish={finish})")
            break
        if probe["gemma_outcome"] == "runaway_finish_length":
            trigger_pass += 1
    if verdict == "pending":
        verdict = ("PASS" if dispatched == 96 and invalid == 0 and trigger_pass == 6
                   else "INCOMPLETE_batch")
    print(json.dumps({
        "verdict": verdict, "dispatched": dispatched, "invalid": invalid,
        "runaway_triggers_completed": trigger_pass,
        "reserved_total_usd": round(reserved_total, 4),
        "decisions_by_originating_judge": decisions_by_judge,
    }, indent=1, sort_keys=True))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
