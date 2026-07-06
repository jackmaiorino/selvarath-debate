"""Experiment design helpers: fixed A/B labeling, the capability grid, a model
registry, and the capability-axis solo-accuracy scorer.
"""
from __future__ import annotations

import hashlib
import random


def position_seed(question_id, transcript_index):
    key = f"{question_id}|{transcript_index}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def position_a_is_correct(question_id, transcript_index):
    """Which side is labeled 'Position A' for the judge — HELD FIXED across budgets.

    The pilot seeded this per (question, transcript, judge, budget), so the A/B
    assignment was re-randomized for every budget, adding noise to the paired
    budget sweep. Here the seed excludes budget (and judge), so a given transcript
    keeps the same A/B labeling across all budgets — a clean paired design.
    """
    return random.Random(position_seed(question_id, transcript_index)).choice([True, False])


# Capability grid model registry. `model_id` strings MUST be re-verified against
# the provider's live catalog before any run (catalogs drift). size_b is nominal
# (MoE models are anchors, not clean dense size points).
MODEL_REGISTRY = {
    "llama-8b":   {"provider": "together", "model_id": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",  "family": "llama", "size_b": 8,   "dense": True},
    "llama-70b":  {"provider": "together", "model_id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",       "family": "llama", "size_b": 70,  "dense": True},
    "llama-405b": {"provider": "together", "model_id": "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo", "family": "llama", "size_b": 405, "dense": True},
    "qwen-7b":    {"provider": "together", "model_id": "Qwen/Qwen2.5-7B-Instruct-Turbo",                "family": "qwen",  "size_b": 7,   "dense": True},
    "qwen-32b":   {"provider": "together", "model_id": "Qwen/Qwen2.5-32B-Instruct",                     "family": "qwen",  "size_b": 32,  "dense": True},
    "qwen-72b":   {"provider": "together", "model_id": "Qwen/Qwen2.5-72B-Instruct-Turbo",               "family": "qwen",  "size_b": 72,  "dense": True},
    "gemma-9b":   {"provider": "together", "model_id": "google/gemma-2-9b-it",                          "family": "gemma", "size_b": 9,   "dense": True},
    "gemma-27b":  {"provider": "together", "model_id": "google/gemma-2-27b-it",                         "family": "gemma", "size_b": 27,  "dense": True},
    "deepseek-v3": {"provider": "together", "model_id": "deepseek-ai/DeepSeek-V3",                      "family": "deepseek", "size_b": 671, "dense": False},
}


def build_grid(judge_keys, debater_keys, budgets, require_debater_ge_judge=False, capability=None):
    """Enumerate (judge, debater, budget) cells for the capability experiment.

    If require_debater_ge_judge is True, keep only cells where the debater is at
    least as capable as the judge (the oversight regime of interest). `capability`
    is an optional dict model_key -> capability score used for that comparison;
    without it, size_b from the registry is used as a fallback proxy.
    """
    def cap(k):
        if capability is not None:
            return capability[k]
        return MODEL_REGISTRY[k]["size_b"]
    cells = []
    for j in judge_keys:
        for d in debater_keys:
            if require_debater_ge_judge and cap(d) < cap(j):
                continue
            for b in budgets:
                cells.append({"judge": j, "debater": d, "budget": b,
                              "gap": cap(d) - cap(j)})
    return cells


def solo_correct(pick, correct_is_a):
    """Capability-axis scorer: given the model's forced-choice pick ('A'/'B') on a
    full-context question where `correct_is_a` says which label held the correct
    answer, did it pick correctly? Returns True/False, or None if pick is invalid.
    """
    if pick not in ("A", "B"):
        return None
    return (pick == "A") == bool(correct_is_a)
