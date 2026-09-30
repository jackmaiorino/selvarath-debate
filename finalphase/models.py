"""Frozen model registry for the final phase.

Prices are published list rates in USD per million tokens (checked 2026-09-30). Batch
rates are half the list rates on OpenAI and Anthropic. Reasoning tokens bill as output.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    model_id: str
    provider: str  # "openai" | "anthropic" | "together"
    price_in: float
    price_out: float
    price_cached_in: float
    batch: bool
    effort: str | None = None  # reasoning effort sent to the provider; None = provider default


MODELS: dict[str, ModelSpec] = {
    m.key: m
    for m in [
        # OpenAI (Responses API)
        ModelSpec("luna", "gpt-5.6-luna", "openai", 0.20, 1.20, 0.02, True),
        ModelSpec("terra", "gpt-5.6-terra", "openai", 2.00, 12.00, 0.20, True),
        ModelSpec("sol", "gpt-5.6-sol", "openai", 4.00, 20.00, 0.40, True),
        ModelSpec("astra", "gpt-6-astra", "openai", 10.00, 50.00, 1.00, True),
        # Anthropic (Messages API)
        ModelSpec("haiku", "claude-haiku-4-5", "anthropic", 1.00, 5.00, 0.10, True),
        ModelSpec("sonnet", "claude-sonnet-5-5", "anthropic", 2.00, 10.00, 0.20, True),
        ModelSpec("opus", "claude-opus-5-5", "anthropic", 4.00, 20.00, 0.20, True),
        ModelSpec("fable", "claude-fable-5-1", "anthropic", 10.00, 50.00, 0.25, True),
        # Together (chat completions, live only)
        ModelSpec("llama70", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "together", 1.04, 1.04, 1.04, False),
        ModelSpec("qwen38", "Qwen/Qwen3.8-2.4T-A95B", "together", 2.00, 6.00, 2.00, False),
        ModelSpec("dspro", "deepseek-ai/DeepSeek-V4-Pro-0813", "together", 1.32, 3.96, 1.32, False),
    ]
}


def spec(key_or_id: str) -> ModelSpec:
    if key_or_id in MODELS:
        return MODELS[key_or_id]
    for m in MODELS.values():
        if m.model_id == key_or_id:
            return m
    raise KeyError(key_or_id)


def cost_usd(m: ModelSpec, input_tokens: int, output_tokens: int, cached_tokens: int = 0, batch: bool = False) -> float:
    """input_tokens includes cached_tokens (OpenAI convention); callers normalise Anthropic usage."""
    uncached = max(0, input_tokens - cached_tokens)
    c = (uncached * m.price_in + cached_tokens * m.price_cached_in + output_tokens * m.price_out) / 1e6
    return c * (0.5 if batch and m.batch else 1.0)
