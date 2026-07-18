"""One-shot builder for rejudge/phase2_preflight_forecast_2026-07-18.json.

Not part of the frozen module surface; a throwaway script kept for reproducibility of the
forecast artifact's tokenizer pins and counted tokens. UNLIKE every other module under
rejudge/, this script downloads pinned Hugging Face tokenizers over the network (dev-only
dependency; no provider/inference API calls of any kind). Run with:

    uv run python scripts/build_phase2_preflight_forecast.py

Requires the ``dev`` dependency group (``transformers``, ``tokenizers``, ``jinja2``) and
network access to huggingface.co. Writes the frozen artifact and prints its canonical sha256.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import jinja2
import tokenizers as tokenizers_pkg
import transformers
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import HfHubHTTPError
from transformers import AutoTokenizer

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase2_capability_corpus as capability_corpus  # noqa: E402
from rejudge import phase2_plan  # noqa: E402
from rejudge import phase2_preflight_forecast as forecast  # noqa: E402
from rejudge import phase2_prompt_bundle  # noqa: E402
from rejudge import phase2_provider_price_snapshot as price_snapshot  # noqa: E402
from rejudge import phase2_role_limits  # noqa: E402


OUTPUT_PATH = REPO_ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-18.json"
CONFLICT_OUTPUT_PATH = (
    REPO_ROOT / "rejudge" / "phase2_preflight_forecast_conflict_2026-07-18.json")

# --- exact tokenizer plan (owner-approved model IDs + repositories only; no discovery) ---------
EXACT_PLAN: dict[str, str] = {
    "Qwen/Qwen2.5-7B-Instruct-Turbo": "Qwen/Qwen2.5-7B-Instruct",
    "google/gemma-4-31B-it": "google/gemma-4-31B-it",
    "openai/gpt-oss-120b": "openai/gpt-oss-120b",
}
PROXY_PLAN: dict[str, tuple[str, str]] = {
    "Qwen/Qwen3.7-Plus": ("Qwen/Qwen3.6-27B", "Qwen/Qwen3.5-9B"),
}
LLAMA_MODEL_ID = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
LLAMA_ATTEMPTS: tuple[str, ...] = (
    "meta-llama/Llama-3.3-70B-Instruct", "meta-llama/Llama-3.1-70B-Instruct",
)
TOKENIZER_FILES: tuple[str, ...] = ("tokenizer.json", "tokenizer_config.json")

CAP_LIMITS_ROLE = forecast.CAPABILITY_LIMITS_ROLE


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_str(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_revision(api: HfApi, repo_id: str) -> str:
    info = api.model_info(repo_id)
    if not info.sha:
        raise RuntimeError(f"{repo_id}: HfApi returned no commit sha")
    return info.sha


def _tokenizer_file_hashes(repo_id: str, revision: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for filename in TOKENIZER_FILES:
        local_path = Path(hf_hub_download(repo_id, filename, revision=revision))
        hashes[filename] = _sha256_file(local_path)
    return hashes


def _count_tokens(tokenizer: Any, system_prompt: str, user_prompt: str) -> int:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    token_ids = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=False)
    return len(token_ids)


def _load_pinned_tokenizer(repo_id: str, revision: str):
    return AutoTokenizer.from_pretrained(repo_id, revision=revision)


def build_forecast() -> tuple[dict[str, Any], bool]:
    """Build the artifact from real, freshly-counted inputs.

    Returns ``(artifact, is_ready)``. ``is_ready`` is True only when the honestly-computed
    ``four_attempt_stress`` clears ``halt_cap_usd``; in that case ``artifact`` is the canonical
    "ready" forecast shape (``forecast.validate_forecast``). Otherwise ``artifact`` is the
    diagnostic conflict-report shape (``forecast.validate_conflict_report``) -- this function
    never fabricates inputs to force ``is_ready`` to True.
    """
    api = HfApi()

    protocol = phase2_plan.load_protocol()
    role_limits_v2, _protocol, snapshot = phase2_role_limits.load_and_validate_v2()
    bundle, _protocol2 = phase2_prompt_bundle.load_and_validate()

    entries = capability_corpus.render_capability_corpus(bundle, protocol, REPO_ROOT)
    corpus_sha = capability_corpus.corpus_canonical_sha256(entries)
    print(f"rendered {len(entries)} capability_qa entries; corpus_sha256={corpus_sha}")

    byte_bound_per_prompt = [
        forecast.byte_reservation_bound(e["system_prompt"], e["user_prompt"]) for e in entries
    ]
    byte_bound_stats = forecast.compute_token_stats(byte_bound_per_prompt)

    tokenizer_pins: dict[str, Any] = {}
    per_model_token_stats: dict[str, Any] = {}

    # --- exact tokenizers ---
    for model_id, repo_id in EXACT_PLAN.items():
        revision = _resolve_revision(api, repo_id)
        print(f"{model_id}: exact tokenizer {repo_id}@{revision}")
        tok = _load_pinned_tokenizer(repo_id, revision)
        file_hashes = _tokenizer_file_hashes(repo_id, revision)
        chat_template = tok.chat_template
        chat_template_sha = _sha256_str(chat_template) if chat_template else None
        counts = [_count_tokens(tok, e["system_prompt"], e["user_prompt"]) for e in entries]
        tokenizer_pins[model_id] = {
            "classification": forecast.CLASSIFICATION_EXACT,
            "tokenizer_repository": repo_id,
            "revision": revision,
            "tokenizer_file_hashes": file_hashes,
            "chat_template_sha256": chat_template_sha,
            "generation_prompt_included": True,
            "counting_method": (
                "transformers.AutoTokenizer.apply_chat_template(messages, "
                "add_generation_prompt=True, tokenize=True, return_dict=False)"
            ),
            "fallback_used": False,
            "notes": (
                f"Pinned public tokenizer for {model_id}'s Together-hosted checkpoint; "
                f"repository {repo_id} loaded successfully at commit revision {revision}."
            ),
        }
        per_model_token_stats[model_id] = {
            "classification": forecast.CLASSIFICATION_EXACT,
            "input_tokens": forecast.compute_token_stats(counts),
            "utf8_byte_reservation_bound": byte_bound_stats,
        }

    # --- proxy tokenizers (Qwen3.7-Plus: hosted, no public tokenizer) ---
    for model_id, (repo_a, repo_b) in PROXY_PLAN.items():
        proxies = []
        proxy_counts: list[list[int]] = []
        for repo_id in (repo_a, repo_b):
            revision = _resolve_revision(api, repo_id)
            print(f"{model_id}: proxy tokenizer {repo_id}@{revision}")
            tok = _load_pinned_tokenizer(repo_id, revision)
            file_hashes = _tokenizer_file_hashes(repo_id, revision)
            chat_template = tok.chat_template
            chat_template_sha = _sha256_str(chat_template) if chat_template else None
            counts = [_count_tokens(tok, e["system_prompt"], e["user_prompt"]) for e in entries]
            proxy_counts.append(counts)
            proxies.append({
                "repository": repo_id,
                "revision": revision,
                "tokenizer_file_hashes": file_hashes,
                "chat_template_sha256": chat_template_sha,
                "generation_prompt_included": True,
                "counting_method": (
                    "transformers.AutoTokenizer.apply_chat_template(messages, "
                    "add_generation_prompt=True, tokenize=True, return_dict=False)"
                ),
                "per_prompt": counts,
            })
        divergent = sum(1 for a, b in zip(proxy_counts[0], proxy_counts[1]) if a != b)
        identical = len(entries) - divergent
        combined = [max(a, b) for a, b in zip(proxy_counts[0], proxy_counts[1])]
        tokenizer_pins[model_id] = {
            "classification": forecast.CLASSIFICATION_PROXY,
            "proxies": proxies,
            "convergence": {
                "all_212_identical": divergent == 0,
                "identical_count": identical,
                "divergent_count": divergent,
            },
            "pricing_rule": (
                "per-prompt max(count_from_Qwen3.6-27B, count_from_Qwen3.5-9B); the larger "
                "of the two proxy counts is used for every pricing scenario"
            ),
            "generation_prompt_included": True,
            "counting_method": (
                "elementwise max of the two proxy tokenizers' apply_chat_template counts"
            ),
            "fallback_used": False,
            "notes": (
                f"{model_id} is a hosted model with no public tokenizer; per the oracle-frozen "
                f"dual-proxy design, counted with both {repo_a} and {repo_b} pinned tokenizers. "
                f"{'All 212 prompts produced identical counts (convergence).' if divergent == 0 else f'{divergent} of 212 prompts diverged; the larger per-prompt count was used.'}"
            ),
        }
        per_model_token_stats[model_id] = {
            "classification": forecast.CLASSIFICATION_PROXY,
            "input_tokens": forecast.compute_token_stats(combined),
            "utf8_byte_reservation_bound": byte_bound_stats,
        }

    # --- Llama: gated tokenizer, documented fallback ---
    attempted_tokenizers = []
    for repo_id in LLAMA_ATTEMPTS:
        try:
            revision = _resolve_revision(api, repo_id)
        except Exception as exc:  # noqa: BLE001 - record whatever HfApi raises, never fabricate
            attempted_tokenizers.append({
                "repository": repo_id, "revision": "unknown",
                "outcome": "model_info_failed", "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        try:
            _load_pinned_tokenizer(repo_id, revision)
            # A successful load must never be silently discarded; this branch would only be
            # reached if HF access policy changed since this script was last run, in which case
            # the exact-tokenizer branch above should be used for this model_id instead.
            raise RuntimeError(
                f"{repo_id}@{revision} loaded successfully; update EXACT_PLAN and this script's "
                "Llama fallback plan instead of silently taking the gated-fallback branch")
        except (HfHubHTTPError, OSError) as exc:
            attempted_tokenizers.append({
                "repository": repo_id, "revision": revision,
                "outcome": "gated_401_unauthenticated", "error": f"{type(exc).__name__}: {exc}",
            })
    print(f"{LLAMA_MODEL_ID}: byte-bound only ({len(attempted_tokenizers)} attempts recorded)")
    tokenizer_pins[LLAMA_MODEL_ID] = {
        "classification": forecast.CLASSIFICATION_BYTE_BOUND,
        "attempted_tokenizers": attempted_tokenizers,
        "fallback_used": True,
        "notes": (
            "Both meta-llama/Llama-3.3-70B-Instruct (the primary tokenizer) and "
            "meta-llama/Llama-3.1-70B-Instruct (the documented public-Llama-3.1 proxy) are "
            "access-gated on Hugging Face and returned an unauthenticated 401 in this build "
            "environment (no HF_TOKEN with grant access configured). Per the frozen fallback "
            "policy, this model's count is byte-bound only: never fabricated, never silently "
            "substituted with a different family's tokenizer."
        ),
    }
    per_model_token_stats[LLAMA_MODEL_ID] = {
        "classification": forecast.CLASSIFICATION_BYTE_BOUND,
        "input_tokens": byte_bound_stats,
        "utf8_byte_reservation_bound": byte_bound_stats,
    }

    # --- output token policy / retry policy ---
    output_ceilings: dict[str, int] = {}
    for model_id in forecast.MODEL_IDS:
        resolved = phase2_role_limits.resolve_request_parameters(
            role_limits_v2, protocol, model_id, CAP_LIMITS_ROLE)
        output_ceilings[model_id] = resolved.effective_max_tokens
    transport = role_limits_v2["request_settings"]["transport"]
    max_retries = int(transport["max_retries"])
    max_attempts = int(transport["max_attempts"])

    # --- scenarios ---
    calls_per_model = capability_corpus.EXPECTED_ENTRY_COUNT

    def prices(model_id: str) -> tuple[Decimal, Decimal]:
        entry = snapshot["models"][model_id]
        return (
            Decimal(str(entry["input_usd_per_million_tokens"])),
            Decimal(str(entry["output_usd_per_million_tokens"])),
        )

    theoretical_minimum_per_model = {}
    theoretical_minimum_total = Decimal(0)
    no_retry_maximum_per_model = {}
    no_retry_maximum_total = Decimal(0)
    no_retry_component_usd: dict[str, Decimal] = {}
    for model_id in forecast.MODEL_IDS:
        total_input_tokens = per_model_token_stats[model_id]["input_tokens"]["total"]
        input_price, output_price = prices(model_id)

        theo_component = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls_per_model,
            output_tokens_per_call=forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL,
            input_price=input_price, output_price=output_price,
        )
        theoretical_minimum_per_model[model_id] = theo_component
        theoretical_minimum_total += Decimal(theo_component["total_usd"])

        ceiling_component = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls_per_model,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        no_retry_maximum_per_model[model_id] = ceiling_component
        no_retry_component_usd[model_id] = Decimal(ceiling_component["total_usd"])
        no_retry_maximum_total += Decimal(ceiling_component["total_usd"])

    def derived_scenario(multiplier: Decimal) -> tuple[dict[str, Any], Decimal]:
        per_model = {}
        total = Decimal(0)
        for model_id in forecast.MODEL_IDS:
            value = no_retry_component_usd[model_id] * multiplier
            per_model[model_id] = {"total_usd": str(value)}
            total += value
        return per_model, total

    planning_per_model, planning_total = derived_scenario(forecast.PLANNING_RETRY_MULTIPLIER)
    stress_per_model, stress_total = derived_scenario(Decimal(max_attempts))

    qwen_id = next(iter(forecast.PROXY_TOKENIZER_MODEL_IDS))
    qwen_input_price, qwen_output_price = prices(qwen_id)
    qwen_byte_component = forecast.compute_scenario_component(
        total_input_tokens=byte_bound_stats["total"], calls=calls_per_model,
        output_tokens_per_call=output_ceilings[qwen_id],
        input_price=qwen_input_price, output_price=qwen_output_price,
    )
    qwen_byte_stress = Decimal(qwen_byte_component["total_usd"]) * Decimal(max_attempts)

    capability_preflight = protocol["materialization_requirements"]["capability_preflight"]
    halt_cap = Decimal(str(capability_preflight["proposed_cap_usd"]))
    is_ready = stress_total < halt_cap
    stress_margin = halt_cap - stress_total
    if not is_ready:
        print(
            f"CONFLICT: four_attempt_stress total {stress_total} does not remain below "
            f"halt_cap_usd {halt_cap} (margin {stress_margin}); writing a diagnostic "
            "conflict-report artifact instead of the canonical 'ready' forecast. This is not "
            "a bug -- see rejudge/phase2_preflight_forecast.py's module docstring."
        )

    protocol_sha = phase2_plan.canonical_sha256(protocol)
    role_limits_sha = phase2_plan.canonical_sha256(role_limits_v2)
    snapshot_sha = phase2_plan.canonical_sha256(snapshot)
    bundle_sha = phase2_plan.canonical_sha256(bundle)

    artifact: dict[str, Any] = {
        "schema_version": forecast.SCHEMA_VERSION if is_ready else forecast.CONFLICT_SCHEMA_VERSION,
        "artifact_id": forecast.ARTIFACT_ID if is_ready else forecast.CONFLICT_ARTIFACT_ID,
        "protocol_id": protocol["protocol_id"],
        "status": forecast.STATUS if is_ready else forecast.CONFLICT_STATUS,
        "execution_authorized": False,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dependency_versions": {
            "transformers": transformers.__version__,
            "tokenizers": tokenizers_pkg.__version__,
            "jinja2": jinja2.__version__,
        },
        "bindings": {
            "protocol": {
                "tracked_path": "rejudge/phase2_protocol.json", "canonical_sha256": protocol_sha,
            },
            "role_limits_v2": {
                "tracked_path": "rejudge/phase2_role_limits_v2_2026-07-18.json",
                "canonical_sha256": role_limits_sha,
            },
            "price_snapshot": {
                "tracked_path": "rejudge/phase2_provider_price_snapshot_2026-07-18.json",
                "canonical_sha256": snapshot_sha,
            },
            "prompt_bundle": {
                "tracked_path": "rejudge/phase2_prompt_bundle.json", "canonical_sha256": bundle_sha,
            },
            "rendered_corpus": {"canonical_sha256": corpus_sha, "entry_count": len(entries)},
        },
        "corpus": {
            "question_count": capability_corpus.EXPECTED_QUESTION_COUNT,
            "mirrored_replicates": capability_corpus.EXPECTED_REPLICATE_COUNT,
            "total_rendered_message_sets": capability_corpus.EXPECTED_ENTRY_COUNT,
            "question_sources": list(protocol["question_set"]["question_sources"]),
            "world_spec_sources": [
                "world_specs/carath_norn.txt", "world_specs/selvarath.txt",
                "world_specs/vethun_sarak.txt",
            ],
            "template_name": capability_corpus.CAPABILITY_TEMPLATE_NAME,
            "rendering_note": (
                "Each of the 106 frozen questions (all_106: the 82 main IDs plus the 24 "
                "calibration-excluded IDs) is rendered twice -- side A (candidate_a=correct, "
                "candidate_b=wrong) and side B (candidates swapped) -- against the frozen "
                "capability_qa system+user templates and the real world document for that "
                "question's world, giving 212 message sets identical across all five models."
            ),
        },
        "tokenizer_pins": tokenizer_pins,
        "per_model_token_stats": per_model_token_stats,
        "corpus_utf8_byte_reservation_bound_per_prompt": byte_bound_per_prompt,
        "output_token_policy": {
            "theoretical_minimum_output_tokens_per_call": (
                forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL),
            "effective_output_ceiling_per_model": output_ceilings,
            "source": (
                "rejudge/phase2_role_limits_v2_2026-07-18.json via "
                "phase2_role_limits.resolve_request_parameters(model, 'capability_qa')"
            ),
        },
        "retry_policy": {
            "max_retries": max_retries, "max_attempts": max_attempts,
            "source": "rejudge/phase2_role_limits_v2_2026-07-18.json request_settings.transport",
        },
        "replace_with_actuals_requirement": (
            "Every scenario in this forecast is a preflight estimate from counted tokens, not "
            "an execution result. Once the capability-preflight stage actually runs, each "
            "call's provider-reported prompt_tokens and completion_tokens (persisted per "
            "rejudge/phase2_role_limits_v2_2026-07-18.json request_settings."
            "response_metadata_to_persist) supersede the corresponding estimate in this "
            "artifact for all downstream cost accounting; this forecast is never treated as "
            "actual spend after real calls exist."
        ),
        "scenarios": {
            "theoretical_minimum": {
                "formula": (
                    "sum over 212 calls per model of (counted input tokens x input price) + "
                    f"({forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL} output tokens x "
                    "212 calls x output price); no retries"
                ),
                "calls_per_model": calls_per_model,
                "output_tokens_per_call_by_model": {
                    model_id: forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL
                    for model_id in forecast.MODEL_IDS
                },
                "per_model": theoretical_minimum_per_model,
                "total_usd": str(theoretical_minimum_total),
            },
            "no_retry_maximum": {
                "formula": (
                    "sum over 212 calls per model of (counted input tokens x input price) + "
                    "(effective per-model-role output ceiling x 212 calls x output price); "
                    "no retries"
                ),
                "calls_per_model": calls_per_model,
                "output_tokens_per_call_by_model": output_ceilings,
                "per_model": no_retry_maximum_per_model,
                "total_usd": str(no_retry_maximum_total),
            },
            "planning_retry_scenario": {
                "formula": "no_retry_maximum x 1.05 (a planning scenario, not a ceiling)",
                "multiplier": str(forecast.PLANNING_RETRY_MULTIPLIER),
                "per_model": planning_per_model,
                "total_usd": str(planning_total),
            },
            "four_attempt_stress": {
                "formula": (
                    "no_retry_maximum x max_attempts (every call charged at the pinned "
                    "four-attempt ceiling)"
                ),
                "multiplier": str(Decimal(max_attempts)),
                "per_model": stress_per_model,
                "total_usd": str(stress_total),
                "qwen_3_7_plus_byte_bound_stress_usd": str(qwen_byte_stress),
                "qwen_3_7_plus_byte_bound_note": (
                    "Qwen/Qwen3.7-Plus's four_attempt_stress figure above uses the conservative "
                    "(larger) dual-proxy token estimate, per the frozen pricing rule. This "
                    "figure instead prices the same scenario from Qwen/Qwen3.7-Plus's own "
                    "UTF-8 byte-reservation bound, as an independent, looser safety comparator."
                ),
            },
        },
        "halt_cap_usd": str(halt_cap),
        "stress_margin_usd": str(stress_margin),
        "caveats": [
            {
                "id": "provider_catalog_observation_disagreement",
                "text": (
                    "An oracle browse on 2026-07-18 reported drifted Gemma/Llama prices and a "
                    "missing Qwen3.7-Plus entry in Together's public catalog; a direct same-day "
                    "fetch (rejudge/phase2_provider_price_snapshot_2026-07-18.json) confirmed "
                    "all five frozen roster models listed at exactly the frozen prices used in "
                    "this forecast. A timestamped roster/price refresh against the live catalog "
                    "is REQUIRED at execution-authorization time; if that refresh disagrees with "
                    "the frozen prices used here, the capability-preflight ask is blocked until "
                    "reconciled, not silently re-priced."
                ),
            },
            {
                "id": "hosted_model_tokenizer_proxy_status",
                "text": (
                    "Qwen/Qwen3.7-Plus is a hosted Together model with no public tokenizer. Its "
                    "token counts in this forecast are a dual-proxy estimate (Qwen/Qwen3.6-27B "
                    "and Qwen/Qwen3.5-9B pinned tokenizers), priced from the larger per-prompt "
                    "count when the two disagree; see tokenizer_pins['Qwen/Qwen3.7-Plus']."
                    "convergence for whether the two proxies actually agreed on all 212 prompts "
                    "in this build. This is never claimed to be an exact count."
                ),
            },
            {
                "id": "reasoning_token_wildcard",
                "text": (
                    "google/gemma-4-31B-it, openai/gpt-oss-120b, and Qwen/Qwen3.7-Plus are "
                    "reasoning models; their effective_request_max_tokens ceiling (4096) is "
                    "believed to bound total completion tokens including any reasoning tokens, "
                    "but provider support for a reasoning-effort/thinking-control request field "
                    "on the Gemma and Qwen3.7-Plus endpoints is explicitly unverified per "
                    "rejudge/phase2_role_limits_v2_2026-07-18.json's reasoning_control_note. If "
                    "any provider bills reasoning tokens separately from or beyond the requested "
                    "max_tokens ceiling, actual completion cost for these three models could "
                    "exceed no_retry_maximum and, in the worst case, four_attempt_stress."
                ),
            },
        ],
    }
    if not is_ready:
        artifact["resolution"] = {
            "required": True,
            "stress_below_halt_cap": False,
            "blocking_scenario": "four_attempt_stress",
            "options": [
                (
                    "Reduce the pinned retry transport max_attempts from 4 to 3 "
                    "(rejudge/phase2_role_limits_v2_2026-07-18.json request_settings.transport), "
                    f"via an append-only v3 role-limits artifact with renewed owner approval; "
                    "at current prices this alone would land four_attempt_stress under "
                    "halt_cap_usd."
                ),
                (
                    "Raise halt_cap_usd above the honestly-computed four_attempt_stress figure "
                    "(with rounding margin), via an explicit protocol amendment to "
                    "materialization_requirements.capability_preflight.proposed_cap_usd."
                ),
                (
                    "Amend the frozen forecast requirement so halt_cap_usd is documented as a "
                    "hard runtime spend ceiling (the runtime accounting ledger halts a live run "
                    "once accumulated spend reaches it) rather than a guarantee that the "
                    "four-attempt worst case can complete in full -- i.e. accept that under "
                    "pathological retries, completion of all 1,060 capability_qa cells is not "
                    "guaranteed within the cap."
                ),
            ],
        }
    return artifact, is_ready


def render_forecast(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def main() -> None:
    artifact, is_ready = build_forecast()
    rendered = render_forecast(artifact)
    output_path = OUTPUT_PATH if is_ready else CONFLICT_OUTPUT_PATH
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    label = "READY forecast" if is_ready else "CONFLICT REPORT (owner resolution required)"
    print(f"wrote {label}: {output_path}; canonical_sha256={phase2_plan.canonical_sha256(artifact)}")
    if not is_ready:
        print(
            f"NOTE: {OUTPUT_PATH} was NOT written. The canonical 'ready' forecast is refused "
            "while four_attempt_stress exceeds halt_cap_usd; see the resolution section in "
            f"{CONFLICT_OUTPUT_PATH} for the owner's concrete options."
        )


if __name__ == "__main__":
    main()
