"""Build the complete blinded adjudicator call panel offline and forecast its cost."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rejudge.api_client import _estimate_usage
from rejudge.phase4_runner import canonical, sha, token_cost, usd
from rejudge.phase4b_labels import messages_for, call_seed, ORDER_SEED, SEED_NAMESPACE


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def write_once_or_identical(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Existing prepared output differs: {path.name}")
    else:
        with path.open("xb") as stream:
            stream.write(data)


def jsonl_bytes(values):
    return "".join(canonical(value) + "\n" for value in values).encode()


def build(inputs: Path, config: dict) -> dict:
    if inputs.is_relative_to(ROOT):
        raise ValueError("Private adjudication prompts must stay outside Git")
    base = json.loads((inputs / "manifest.json").read_bytes())
    for name, binding in base["outputs"].items():
        expected = binding if isinstance(binding, str) else binding["sha256"]
        if sha(inputs / name) != expected:
            raise ValueError("Prepared source changed: " + name)
    claims = rows(inputs / "blind_claims.jsonl")
    world_rows = rows(inputs / "worlds_private.jsonl")
    worlds = {w["world_sha256"]: w["world_document"] for w in world_rows}
    if len(worlds) != len(world_rows) or any(hashlib.sha256(text.encode()).hexdigest() != key for key, text in worlds.items()):
        raise ValueError("World identity mismatch")
    oracle = base["oracle_contract"]["system_prompt"]
    if hashlib.sha256(oracle.encode()).hexdigest() != base["template_provenance"]["oracle_system_prompt_sha256"]:
        raise ValueError("Frozen oracle contract mismatch")
    models = config["models"]
    if len(models) != 2 or len(set(models)) != 2:
        raise ValueError("Exactly two independent model endpoints are required")
    if len({c["claim_id"] for c in claims}) != len(claims):
        raise ValueError("Duplicate claim identity")
    packets, calls = [], []
    by_model = defaultdict(lambda: {"calls": 0, "estimated_input_tokens": 0,
                                   "input_cost_nanodollars": 0, "max_estimated_context_tokens": 0})
    for claim in sorted(claims, key=lambda c: c["claim_id"]):
        expected_id = hashlib.sha256((claim["world_sha256"] + "|" + claim["exact_claim"]).encode()).hexdigest()
        if expected_id != claim["claim_id"]:
            raise ValueError("Claim identity mismatch")
        messages = messages_for(worlds[claim["world_sha256"]], claim["exact_claim"], oracle)
        digest = hashlib.sha256(canonical(messages).encode()).hexdigest()
        packet = {"packet_id": claim["claim_id"], "claim_id": claim["claim_id"],
                  "messages": messages, "messages_sha256": digest}
        packets.append(packet)
        for model in models:
            settings = config["model_settings"][model]
            call = {"cell_id": claim["claim_id"] + ":" + model, "claim_id": claim["claim_id"],
                    "judge": model, "packet_id": packet["packet_id"], "messages_sha256": digest,
                    "seed": call_seed(claim["claim_id"], model), "stream": False,
                    **{k: settings[k] for k in ("temperature", "top_p", "max_tokens", "reasoning_effort")}}
            calls.append(call)
            p, _ = _estimate_usage(messages, settings["max_tokens"])
            if p + settings["max_tokens"] > settings["context_length"]:
                raise ValueError("A blinded request exceeds configured context")
            stats = by_model[model]
            stats["calls"] += 1
            stats["estimated_input_tokens"] += p
            stats["input_cost_nanodollars"] += token_cost(p, 0, config["prices_per_million"][model])
            stats["max_estimated_context_tokens"] = max(stats["max_estimated_context_tokens"], p + settings["max_tokens"])
    # Keep the two independent adjudications of each claim in the same scheduling block.
    blocks = [calls[i:i+2] for i in range(0, len(calls), 2)]
    rng = random.Random(ORDER_SEED)
    rng.shuffle(blocks)
    for block in blocks:
        rng.shuffle(block)
    calls = [call for block in blocks for call in block]
    write_once_or_identical(inputs / "claim_packets.jsonl", jsonl_bytes(packets))
    write_once_or_identical(inputs / "calls.jsonl", jsonl_bytes(calls))
    forecasts = {}
    for completion_tokens in (512, 2048, 4096, 8192):
        forecasts[str(completion_tokens)] = round(sum(usd(
            stats["input_cost_nanodollars"] + token_cost(0, stats["calls"] * completion_tokens,
                                                       config["prices_per_million"][model]))
            for model, stats in by_model.items()), 6)
    outputs = {name: {"sha256": sha(inputs / name), "bytes": (inputs / name).stat().st_size}
               for name in ["manifest.json", *base["outputs"], "claim_packets.jsonl", "calls.jsonl"]}
    manifest = {"schema_version": "phase4b_blinded_adjudication_panel_v1", "stage": "4B",
                "adjudication_calls": len(calls), "distinct_claims": len(claims),
                "models": models, "model_settings": config["model_settings"], "outputs": outputs,
                "oracle_contract": oracle, "call_seed_namespace": SEED_NAMESPACE, "order_seed": ORDER_SEED,
                "prompt_script_sha256": sha(ROOT / "rejudge/phase4b_labels.py"),
                "preparation_script_sha256": sha(Path(__file__)),
                "prices_per_million": config["prices_per_million"],
                "forecast": {"by_model": dict(by_model),
                             "cost_usd_by_assumed_total_completion_tokens_per_call": forecasts,
                             "note": "Input is a conservative UTF-8 estimator, not measured tokenizer usage. Completion assumptions include reasoning tokens once. Excludes preflight, retries, and unknown delivery; no cache discount. These are scenarios, not guaranteed costs."},
                "input_contract": "Send only packet.messages and frozen call settings. Never send provenance, original replies, identities, answer keys or outcomes.",
                "recipient_calls_authorized": False}
    write_once_or_identical(inputs / "adjudication_manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    return {"distinct_claims": len(claims), "adjudication_calls": len(calls),
            "input_manifest_sha256": sha(inputs / "adjudication_manifest.json"), "forecast": manifest["forecast"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.inputs.resolve(), json.loads(args.config.read_bytes())), indent=2))


if __name__ == "__main__":
    main()
