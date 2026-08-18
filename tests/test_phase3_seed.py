"""Engineering gate 4: judgment_seed extended with debater_model and namespace.

The frozen protocol's decisions.execution_semantics.seed_policy commits the phase-3 seed
derivation to include question, transcript index, judge model, condition/budget, replicate
index, DEBATER MODEL, and the PROTOCOL NAMESPACE. Phase-2's rejudge/config.py judgment_seed
omitted the last two. This file pins that the extension is purely additive: every phase-2
call, which never supplies the new keyword-only parameters, must keep producing the exact
seed value phase-2 already baked into frozen results.

The golden value below was computed from the UNMODIFIED phase-2 judgment_seed (equivalently,
straight from hashlib.md5, independent of any code in this repo) BEFORE this extension was
written, over the fixed six-part tuple
("CN-001", 1, "meta-llama/Llama-3.3-70B-Instruct-Turbo", 2, "clean", 0):

    key = "CN-001|1|meta-llama/Llama-3.3-70B-Instruct-Turbo|2|clean|0"
    int(hashlib.md5(key.encode()).hexdigest()[:8], 16) == 1099264292
"""
import json
from pathlib import Path

from rejudge import config, judge_loop

GOLDEN_SEED = 1099264292

FIXED_ARGS = ("CN-001", 1, "meta-llama/Llama-3.3-70B-Instruct-Turbo", 2, "clean", 0)


def test_golden_value_regression_legacy_call_is_byte_identical():
    """A phase-2-style call (no debater_model, no namespace) must reproduce the exact
    seed value pinned before this extension existed, proving the extension changed
    nothing for legacy callers."""
    assert config.judgment_seed(*FIXED_ARGS) == GOLDEN_SEED


def test_debater_model_changes_the_seed():
    assert config.judgment_seed(
        *FIXED_ARGS, debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo") != GOLDEN_SEED


def test_namespace_changes_the_seed():
    assert config.judgment_seed(
        *FIXED_ARGS,
        namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab") != GOLDEN_SEED


def test_two_debaters_at_an_otherwise_identical_slot_get_distinct_seeds():
    s_llama = config.judgment_seed(*FIXED_ARGS, debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    s_qwen = config.judgment_seed(*FIXED_ARGS, debater_model="Qwen/Qwen3.7-Plus")
    assert s_llama != s_qwen
    # And both still differ from the legacy (no debater_model) value.
    assert s_llama != GOLDEN_SEED
    assert s_qwen != GOLDEN_SEED


def test_same_inputs_always_reproduce_the_same_seed():
    kwargs = dict(debater_model="Qwen/Qwen3.7-Plus",
                  namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")
    a = config.judgment_seed(*FIXED_ARGS, **kwargs)
    b = config.judgment_seed(*FIXED_ARGS, **kwargs)
    assert a == b
    # The legacy call is likewise stable across repeats.
    assert config.judgment_seed(*FIXED_ARGS) == config.judgment_seed(*FIXED_ARGS) == GOLDEN_SEED


def test_debater_model_and_namespace_together_differ_from_either_alone():
    only_debater = config.judgment_seed(*FIXED_ARGS, debater_model="Qwen/Qwen3.7-Plus")
    only_namespace = config.judgment_seed(
        *FIXED_ARGS, namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")
    both = config.judgment_seed(
        *FIXED_ARGS, debater_model="Qwen/Qwen3.7-Plus",
        namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")
    assert len({only_debater, only_namespace, both, GOLDEN_SEED}) == 4


# --- threading through run_judgment (engineering gate 4, step 2) --------------------------

class ScriptedClient:
    """Returns scripted responses by kind; records every call. No network calls."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", **kwargs):
        self.calls.append({"kind": kind, "seed": seed, **kwargs})
        v = self.script[kind]
        return v.pop(0) if isinstance(v, list) else v


def _budget0_transcript():
    return {"question_id": "CN-001", "transcript_index": 1, "question": "Test question?",
            "correct_answer": "Answer A", "wrong_answer": "Answer B", "debate_transcript": []}


def _protocol():
    return config.load_protocol()


VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"


def test_run_judgment_omits_debater_model_and_namespace_by_default_unchanged_seed():
    client = ScriptedClient({"verdict": VERDICT})
    rec = judge_loop.run_judgment(_budget0_transcript(), "DOC", config.ARMS["clean"], 0, 0,
                                  client, _protocol())
    expected = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 0, "clean", 0)
    assert rec["seed"] == expected


def test_run_judgment_threads_debater_model_and_namespace_into_the_record_seed():
    client = ScriptedClient({"verdict": VERDICT})
    rec = judge_loop.run_judgment(
        _budget0_transcript(), "DOC", config.ARMS["clean"], 0, 0, client, _protocol(),
        debater_model="Qwen/Qwen3.7-Plus",
        namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")
    expected = config.judgment_seed(
        "CN-001", 1, config.JUDGE_MODEL, 0, "clean", 0,
        debater_model="Qwen/Qwen3.7-Plus",
        namespace="phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")
    assert rec["seed"] == expected
    default_seed = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 0, "clean", 0)
    assert rec["seed"] != default_seed
