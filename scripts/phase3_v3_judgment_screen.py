"""Stage-1 judgment-shaped completion triage (frozen plan phase3_v3_judgment_screen_plan).

Renders real protocol-composed upper-tail prompts from the frozen MAIN transcript bundle,
calls each configured judge non-streaming at its candidate max_tokens, and records validity
telemetry only (finish reason, token counts, empty status, parser validity, response hash).
Verdict direction and response text are never logged. Rejects a configuration on its first
invalid probe. Hard $2.50 reservation ceiling; refuses dispatch beyond it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import parsers, phase3_plan, phase3_v3_inputs, phase3_v3_static_prompts  # noqa: E402
from rejudge.phase2_query_gate import MalformedCheckerOutput, parse_checker_output  # noqa: E402

PLAN_PATH = REPO_ROOT / "rejudge/phase3_v3_judgment_screen_plan_2026-08-27.json"
PROTOCOL_PATH = REPO_ROOT / "rejudge/phase3_protocol_v3_r4.json"
PROMPT_BUNDLE_PATH = REPO_ROOT / "rejudge/phase2_prompt_bundle.json"
PRICE_SNAPSHOT_PATH = REPO_ROOT / "rejudge/phase3_v3_price_snapshot_r8_2026-08-26.json"
CORPUS_ROOT = REPO_ROOT / "rejudge/output/phase3_v3_exact_tokenizer_corpus_r5_2026-08-25"
TOKENIZER_ROOT = REPO_ROOT / "rejudge/output/phase3_v3_tokenizers"
MAIN_BUNDLE = Path(
    "E:/selvarath-archive/phase3-materialization-2026-08-18/"
    "phase3_transcript_bundle_main_2026-08-18.json")
USAGE_LOG = REPO_ROOT / "rejudge/output/phase3_v3_screen_usage_2026-08-27.jsonl"
RESULTS_LOG = REPO_ROOT / "rejudge/output/phase3_v3_screen_results_2026-08-27.jsonl"
CEILING_USD = 2.5
PROBES_PER_CONFIGURATION = 24
CAP_UTILIZATION_LIMIT = 0.80

MODEL_DIRS = {
    "Qwen/Qwen3.8-2.4T-A95B": "Qwen--Qwen3.8-2.4T-A95B",
    "Qwen/Qwen3.5-9B": "Qwen--Qwen3.5-9B",
    "google/gemma-4-31B-it": "google--gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": "meta-llama--Llama-3.3-70B-Instruct-Turbo",
}
TOKENIZER_DIRS = {
    "Qwen/Qwen3.8-2.4T-A95B": "Qwen--Qwen3.8-2.4T-A95B-exact",
    "Qwen/Qwen3.5-9B": "Qwen--Qwen3.5-9B-exact",
    "google/gemma-4-31B-it": "google--gemma-4-31B-it-exact",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": "meta-llama--Llama-3.3-70B-Instruct-exact",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _world_documents() -> dict[str, str]:
    documents = {}
    for path in sorted((REPO_ROOT / "world_specs").glob("*.txt")):
        documents[path.stem] = path.read_text(encoding="utf-8")
    return documents


def main(argv: list[str] | None = None) -> int:
    parser_cli = argparse.ArgumentParser(description=__doc__)
    parser_cli.add_argument("--dry-run", action="store_true",
                            help="select and render everything; no provider calls")
    args = parser_cli.parse_args(argv)

    plan = _load_json(PLAN_PATH)
    protocol = phase3_plan.load_protocol(PROTOCOL_PATH)
    prompt_bundle = _load_json(PROMPT_BUNDLE_PATH)
    prices = _load_json(PRICE_SNAPSHOT_PATH)["models"]
    world_documents = _world_documents()

    bundle = _load_json(MAIN_BUNDLE)
    by_key = dict(phase3_v3_inputs.transcript_entries(
        bundle, expected_count=phase3_v3_inputs.TRANSCRIPT_BUNDLE_COUNTS["main"]))
    print(f"main transcripts indexed: {len(by_key)}")

    from transformers import AutoTokenizer

    tokenizers = {
        model: AutoTokenizer.from_pretrained(str(TOKENIZER_ROOT / directory))
        for model, directory in TOKENIZER_DIRS.items()
    }

    reserved_total = 0.0
    summary: dict[str, dict] = {}

    def build_probes(configuration: dict) -> list[dict]:
        model = configuration["model"]
        role = configuration["role"]
        corpus_dir = CORPUS_ROOT / MODEL_DIRS[model] / "main"
        counts: dict[str, int] = {}
        for line in (corpus_dir / "judge_verdict.counts.jsonl").read_text(
                encoding="utf-8").splitlines():
            row = json.loads(line)
            counts[row["prompt_key"]] = int(row["prompt_tokens"])
        meta: dict[str, dict] = {}
        for line in (corpus_dir / "judge_verdict.rendered.jsonl").read_text(
                encoding="utf-8").splitlines():
            row = json.loads(line)
            meta[row["prompt_key"]] = row
        ordered = sorted(counts, key=lambda key: (-counts[key], key))
        probes = []
        for prompt_key in ordered:
            if len(probes) >= PROBES_PER_CONFIGURATION:
                break
            row = meta[prompt_key]
            entry = by_key.get(row["transcript_key"])
            if entry is None:
                raise SystemExit(f"transcript missing for corpus key {row['transcript_key']}")
            variant = row["variant_id"]
            _prefix, condition_id, side_token = variant.split("::")
            if role == "query_checker" and condition_id == "b0":
                continue  # checker calls exist only under non-zero query budgets
            messages = phase3_v3_static_prompts.render_static_messages(
                protocol=protocol, prompt_bundle=prompt_bundle,
                world_documents=world_documents, transcript_entry=entry,
                billed_model=model, role="judge_verdict", variant_id=variant)
            text, token_count = phase3_v3_static_prompts.render_chat_prompt(
                tokenizers[model], messages)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest != row["rendered_prompt_sha256"]:
                raise SystemExit(
                    f"rendered prompt hash mismatch for {prompt_key}: screen aborted unspent")
            if role == "query_checker":
                # The checker prompt embeds the mirrored positions; the source judge names
                # whose query stream is checked and is prompt-shape-neutral. Recorded.
                checker_variant = (
                    f"query_checker::Qwen/Qwen3.8-2.4T-A95B::{condition_id}::{side_token}")
                messages = phase3_v3_static_prompts.render_static_messages(
                    protocol=protocol, prompt_bundle=prompt_bundle,
                    world_documents=world_documents, transcript_entry=entry,
                    billed_model=model, role="query_checker",
                    variant_id=checker_variant)
                _text, token_count = phase3_v3_static_prompts.render_chat_prompt(
                    tokenizers[model], messages)
                variant = checker_variant
            probes.append({
                "prompt_key": prompt_key, "variant_id": variant,
                "messages": messages, "prompt_tokens_exact": token_count,
            })
        return probes

    client = None
    if not args.dry_run:
        from together import Together

        client = Together()

    for configuration in plan["configurations_stage_1"]:
        config_id = configuration["id"]
        model = configuration["model"]
        max_tokens = int(configuration["max_tokens"])
        price = prices[model]
        probes = build_probes(configuration)
        outcome = {"probes_dispatched": 0, "invalid": 0, "over_80pct_cap": 0,
                   "verdict": "pending"}
        summary[config_id] = outcome
        print(f"[{config_id}] {len(probes)} probes rendered; "
              f"max prompt {max(p['prompt_tokens_exact'] for p in probes)} tokens")
        if args.dry_run:
            outcome["verdict"] = "dry_run"
            continue
        for index, probe in enumerate(probes):
            reservation = (
                probe["prompt_tokens_exact"] * price["input_usd_per_million"]
                + max_tokens * price["output_usd_per_million"]) / 1_000_000
            if reserved_total + reservation > CEILING_USD:
                outcome["verdict"] = "ceiling_stop"
                print(f"[{config_id}] ceiling stop at probe {index}")
                break
            reserved_total += reservation
            seed = int(hashlib.sha256(
                probe["prompt_key"].encode("utf-8")).hexdigest()[:8], 16)
            started = _utc_now()
            try:
                response = client.chat.completions.create(
                    model=model, messages=probe["messages"], max_tokens=max_tokens,
                    temperature=0.0, seed=seed)
            except Exception as exc:  # noqa: BLE001 - transport failures are recorded, not retried
                _append(USAGE_LOG, {
                    "ts": started, "config": config_id, "prompt_key": probe["prompt_key"],
                    "reserved_usd": reservation, "actual_usd": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                outcome.setdefault("transport_errors", 0)
                outcome["transport_errors"] += 1
                continue
            usage = response.usage
            text = response.choices[0].message.content or ""
            finish = str(response.choices[0].finish_reason)
            actual = (usage.prompt_tokens * price["input_usd_per_million"]
                      + usage.completion_tokens * price["output_usd_per_million"]) / 1_000_000
            _append(USAGE_LOG, {
                "ts": started, "config": config_id, "prompt_key": probe["prompt_key"],
                "reserved_usd": reservation, "actual_usd": actual,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens, "finish_reason": finish})
            outcome["probes_dispatched"] += 1
            empty = not text.strip()
            if configuration["role"] == "query_checker":
                try:
                    parse_checker_output(text)
                    parse_valid = True
                except MalformedCheckerOutput:
                    parse_valid = False
            else:
                parse_valid = parsers.parse_both(text)["strict"]["verdict"] is not None
            invalid = empty or not parse_valid
            over_cap = usage.completion_tokens > CAP_UTILIZATION_LIMIT * max_tokens
            _append(RESULTS_LOG, {
                "ts": started, "config": config_id, "prompt_key": probe["prompt_key"],
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "finish_reason": finish, "empty": empty, "parse_valid": parse_valid,
                "invalid": invalid,
                "over_80pct_cap": over_cap,
                "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
            if invalid:
                outcome["invalid"] += 1
                outcome["verdict"] = "REJECTED_first_invalid"
                print(f"[{config_id}] REJECTED at probe {index} "
                      f"(empty={empty} parse_valid={parse_valid} finish={finish} "
                      f"ct={usage.completion_tokens})")
                break
            if over_cap:
                outcome["over_80pct_cap"] += 1
        if outcome["verdict"] == "pending":
            outcome["verdict"] = (
                "PASS_stage1" if outcome["over_80pct_cap"] == 0
                else "REJECTED_cap_utilization")
        print(f"[{config_id}] {outcome}")

    print(json.dumps({"reserved_total_usd": round(reserved_total, 4),
                      "summary": summary}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
