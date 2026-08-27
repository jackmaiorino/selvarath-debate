"""Stage-1 judgment-shaped completion triage (frozen plan phase3_v3_judgment_screen_plan).

Renders real protocol-composed upper-tail prompts from the INDEPENDENT screening bank (the
original pilot's 318 transcripts in data/transcripts.jsonl, which no phase-3 measurement
input touches), calls each configured judge non-streaming at its candidate max_tokens, and
records validity telemetry only (finish reason, token counts, empty status, parser
validity, response hash). Verdict direction and response text are never logged. Rejects a
configuration on its first invalid probe; a batch that cannot evaluate all 24 probes within
the bounded transport retries is INCOMPLETE, never a pass. Hard $2.50 reservation ceiling.
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
SCREEN_BANK_PATH = REPO_ROOT / "data/transcripts.jsonl"
TRANSPORT_RETRIES = 3
TRANSPORT_BACKOFF_SECONDS = 30
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

# Stage 2 (owner-approved 2026-08-27): 0-of-96 distribution-matched confirmation for the
# stage-1 passers plus 24-probe upper-tail triage of the two weak-slot candidates. Every
# stage-2 configuration carries its own reservation ceiling; the stage envelope is $14.
STAGE2_CEILING_USD = 14.0
STAGE2_CONFIRMATION_PROBES = 96
STAGE2_CONFIGURATIONS = [
    {"id": "qwen38-verdict-8192-confirm", "model": "Qwen/Qwen3.8-2.4T-A95B",
     "role": "judge_verdict", "max_tokens": 8192, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 8.0},
    {"id": "gemma4-verdict-4096-confirm", "model": "google/gemma-4-31B-it",
     "role": "judge_verdict", "max_tokens": 4096, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 3.0},
    {"id": "gemma4-checker-4096-confirm", "model": "google/gemma-4-31B-it",
     "role": "query_checker", "max_tokens": 4096, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 1.5},
    {"id": "llama-verdict-512-confirm", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
     "role": "judge_verdict", "max_tokens": 512, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 1.0},
    {"id": "gemma4e4b-verdict-8192-triage", "model": "google/gemma-4-E4B-it",
     "role": "judge_verdict", "max_tokens": 8192, "probes": 24, "mode": "upper_tail",
     "config_ceiling_usd": 0.5,
     "compose_as": "Qwen/Qwen3.8-2.4T-A95B", "count_with": "google/gemma-4-31B-it",
     "price_as": "google/gemma-4-31B-it",
     "note": "catalog lists E4B unpriced; reservations assume gemma-4-31B rates"},
    {"id": "qwen35_9b-verdict-16384-triage", "model": "Qwen/Qwen3.5-9B",
     "role": "judge_verdict", "max_tokens": 16384, "probes": 24, "mode": "upper_tail",
     "config_ceiling_usd": 0.3},
]

# Stage 3 (owner-approved 2026-08-27 "Full path (Recommended)"): raised-cap re-screen of
# the two thinking-verdict models. Each raised cap is a NEW admission unit under the
# frozen definition, so each gets its own fresh distribution-matched 0-of-96
# confirmation, excluding every probe used by the model's earlier stages (Codex ruling:
# fresh probes, not reuse, so the confirmation is not adaptive to observed failures).
STAGE3_CEILING_USD = 12.5
STAGE3_CONFIGURATIONS = [
    {"id": "qwen38-verdict-16384", "model": "Qwen/Qwen3.8-2.4T-A95B",
     "role": "judge_verdict", "max_tokens": 16384, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 11.2,
     "exclude_prior": ("qwen38-verdict-8192", "qwen38-verdict-8192-confirm")},
    {"id": "gemma4-verdict-8192", "model": "google/gemma-4-31B-it",
     "role": "judge_verdict", "max_tokens": 8192, "probes": 96, "mode": "distribution",
     "config_ceiling_usd": 1.3,
     "exclude_prior": ("gemma4-verdict-4096", "gemma4-verdict-4096-confirm")},
]

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
    parser_cli.add_argument("--resume", action="store_true",
                            help="skip probes already evaluated in the results log")
    parser_cli.add_argument("--stage", type=int, default=1, choices=(1, 2, 3),
                            help="1 = triage of the plan's configurations; "
                                 "2 = owner-approved confirmation plus weak-slot triage; "
                                 "3 = owner-approved raised-cap re-screen")
    args = parser_cli.parse_args(argv)

    plan = _load_json(PLAN_PATH)
    protocol = phase3_plan.load_protocol(PROTOCOL_PATH)
    prompt_bundle = _load_json(PROMPT_BUNDLE_PATH)
    prices = _load_json(PRICE_SNAPSHOT_PATH)["models"]
    world_documents = _world_documents()

    from rejudge.phase2_execution import canonical_sha256

    bank_bytes = SCREEN_BANK_PATH.read_bytes()
    bank_sha256 = hashlib.sha256(bank_bytes).hexdigest()
    entries: list[dict] = []
    for line in bank_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        entries.append({
            "debater_model": payload["debater_model"],
            "question_id": payload["question_id"],
            "transcript_index": payload["transcript_index"],
            "transcript_sha256": canonical_sha256(payload),
            "transcript_payload": payload,
        })
    print(f"independent screening bank: {len(entries)} pilot transcripts, "
          f"sha256 {bank_sha256[:16]}")

    from transformers import AutoTokenizer

    tokenizers = {
        model: AutoTokenizer.from_pretrained(str(TOKENIZER_ROOT / directory))
        for model, directory in TOKENIZER_DIRS.items()
    }

    reserved_total = 0.0
    summary: dict[str, dict] = {}

    def build_probes(configuration: dict) -> list[dict]:
        """Render every candidate prompt for this configuration, then select either the
        upper tail (top-N by exact token count) or a deterministic distribution-matched
        stride sample of N across the full length ordering, excluding probes already
        evaluated in earlier stages. Tie-breaks on the probe id everywhere."""
        model = configuration["model"]
        role = configuration["role"]
        compose_as = configuration.get("compose_as", model)
        count_with = configuration.get("count_with", model)
        probe_count = int(configuration.get("probes", PROBES_PER_CONFIGURATION))
        mode = configuration.get("mode", "upper_tail")
        if role == "query_checker":
            variants = [
                "query_checker::Qwen/Qwen3.8-2.4T-A95B::sequential_b2::side0",
                "query_checker::Qwen/Qwen3.8-2.4T-A95B::sequential_b2::side1",
            ]
            render_role = "query_checker"
        else:
            variants = ["judge_verdict::b0::side0", "judge_verdict::b0::side1"]
            render_role = "judge_verdict"
        candidates = []
        for entry in entries:
            for variant in variants:
                messages = phase3_v3_static_prompts.render_static_messages(
                    protocol=protocol, prompt_bundle=prompt_bundle,
                    world_documents=world_documents, transcript_entry=entry,
                    billed_model=compose_as, role=render_role, variant_id=variant)
                text, token_count = phase3_v3_static_prompts.render_chat_prompt(
                    tokenizers[count_with], messages)
                probe_id = hashlib.sha256(
                    f"{model}|{render_role}|{variant}|{entry['transcript_sha256']}".encode(
                        "utf-8")).hexdigest()
                candidates.append({
                    "prompt_key": probe_id, "variant_id": variant,
                    "messages": messages, "prompt_tokens_exact": token_count,
                    "rendered_prompt_sha256": hashlib.sha256(
                        text.encode("utf-8")).hexdigest(),
                })
        candidates.sort(
            key=lambda probe: (-probe["prompt_tokens_exact"], probe["prompt_key"]))
        fresh = [probe for probe in candidates
                 if (configuration["id"], probe["prompt_key"]) not in evaluated
                 and not any((prior_config, probe["prompt_key"]) in evaluated
                             for prior_config in stage1_alias.get(
                                 configuration["id"], ()))]
        if mode == "upper_tail":
            return fresh[:probe_count]
        # Distribution-matched: a deterministic stride sample across the full length
        # ordering, so confirmation covers the whole prompt-length distribution rather
        # than only the tail.
        if len(fresh) <= probe_count:
            return fresh
        stride = len(fresh) / probe_count
        return [fresh[int(index * stride)] for index in range(probe_count)]

    client = None
    if not args.dry_run:
        from together import Together

        # Thinking-model generations at 4-8k completion tokens legitimately run for
        # minutes; the SDK default read timeout misclassifies them as transport failures
        # (the first live batch lost 10 probes to exactly that). 900s mirrors the canary
        # client's per-call wall-clock ceiling territory.
        client = Together(timeout=900.0)

    stage1_alias = {
        configuration["id"]: (configuration["id"].rsplit("-confirm", 1)[0],)
        for configuration in STAGE2_CONFIGURATIONS
        if configuration["id"].endswith("-confirm")
    }
    for configuration in STAGE3_CONFIGURATIONS:
        stage1_alias[configuration["id"]] = tuple(configuration["exclude_prior"])
    evaluated: set[tuple[str, str]] = set()
    prior_tallies: dict[str, dict[str, int]] = {}
    if RESULTS_LOG.exists():
        for line in RESULTS_LOG.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            evaluated.add((row["config"], row["prompt_key"]))
            tally = prior_tallies.setdefault(
                row["config"], {"probes_dispatched": 0, "invalid": 0,
                                "over_80pct_cap": 0})
            tally["probes_dispatched"] += 1
            tally["invalid"] += int(bool(row["invalid"]))
            tally["over_80pct_cap"] += int(bool(row["over_80pct_cap"]))

    stage_ceiling = {
        1: CEILING_USD, 2: STAGE2_CEILING_USD, 3: STAGE3_CEILING_USD}[args.stage]
    configurations = {
        1: plan["configurations_stage_1"], 2: STAGE2_CONFIGURATIONS,
        3: STAGE3_CONFIGURATIONS}[args.stage]
    for configuration in configurations:
        config_id = configuration["id"]
        model = configuration["model"]
        max_tokens = int(configuration["max_tokens"])
        price = prices[configuration.get("price_as", model)]
        config_ceiling = float(
            configuration.get("config_ceiling_usd", stage_ceiling))
        config_reserved = 0.0
        probes = build_probes(configuration)
        carried = prior_tallies.get(
            config_id, {"probes_dispatched": 0, "invalid": 0, "over_80pct_cap": 0})
        outcome = {**carried, "verdict": "pending"}
        summary[config_id] = outcome
        if args.resume and outcome["invalid"] > 0:
            outcome["verdict"] = "REJECTED_first_invalid"
            print(f"[{config_id}] already rejected in a prior batch")
            continue
        if args.resume:
            probes = [probe for probe in probes
                      if (config_id, probe["prompt_key"]) not in evaluated]
        if probes:
            print(f"[{config_id}] {len(probes)} probes rendered; "
                  f"max prompt {max(p['prompt_tokens_exact'] for p in probes)} tokens")
        else:
            print(f"[{config_id}] all probes already evaluated")
        if args.dry_run:
            outcome["verdict"] = "dry_run"
            continue
        for index, probe in enumerate(probes):
            reservation = (
                probe["prompt_tokens_exact"] * price["input_usd_per_million"]
                + max_tokens * price["output_usd_per_million"]) / 1_000_000
            if (reserved_total + reservation > stage_ceiling
                    or config_reserved + reservation > config_ceiling):
                outcome["verdict"] = "ceiling_stop"
                print(f"[{config_id}] ceiling stop at probe {index}")
                break
            reserved_total += reservation
            config_reserved += reservation
            seed = int(hashlib.sha256(
                probe["prompt_key"].encode("utf-8")).hexdigest()[:8], 16)
            started = _utc_now()
            response = None
            for attempt in range(TRANSPORT_RETRIES):
                try:
                    response = client.chat.completions.create(
                        model=model, messages=probe["messages"], max_tokens=max_tokens,
                        temperature=0.0, seed=seed)
                    break
                except Exception as exc:  # noqa: BLE001 - bounded transport retry
                    _append(USAGE_LOG, {
                        "ts": _utc_now(), "config": config_id,
                        "prompt_key": probe["prompt_key"], "attempt": attempt,
                        "reserved_usd": reservation, "actual_usd": None,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    if attempt + 1 < TRANSPORT_RETRIES:
                        import time

                        time.sleep(TRANSPORT_BACKOFF_SECONDS)
            if response is None:
                # A probe that cannot complete within the bounded retries makes the
                # batch INCOMPLETE: fewer than 24 evaluated probes is never a pass.
                outcome["verdict"] = "INCOMPLETE_batch_transport"
                print(f"[{config_id}] INCOMPLETE at probe {index}: transport exhausted")
                break
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
                "variant_id": probe["variant_id"],
                "rendered_prompt_sha256": probe["rendered_prompt_sha256"],
                "screen_bank_sha256": bank_sha256,
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
            target = int(configuration.get("probes", PROBES_PER_CONFIGURATION))
            if outcome["probes_dispatched"] < target:
                outcome["verdict"] = "INCOMPLETE_batch"
            elif outcome["over_80pct_cap"] == 0:
                outcome["verdict"] = f"PASS_stage{args.stage}"
            else:
                outcome["verdict"] = "REJECTED_cap_utilization"
        print(f"[{config_id}] {outcome}")

    print(json.dumps({"reserved_total_usd": round(reserved_total, 4),
                      "summary": summary}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
