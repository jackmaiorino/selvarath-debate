"""Arm definitions, protocol loading, and the seed scheme for the re-judge."""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from analysis.infra.design import position_a_is_correct

JUDGE_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
DEFAULT_REPLICATES = 2
PLACEBO_TEXT = ("ORACLE PLACEBO: no factual verification was performed for this query. "
                "This message contains no evidence about the world document.")
DEFAULT_PROTOCOL_PATH = Path(__file__).resolve().parents[1] / "experiment_protocol.json"

_PILOT_PHRASING_LINE = '- Be phrased in the form: "Is it stated in the text that [specific factual claim]?"'
_CLEAN_PHRASING_LINE = ('- Be given as ONLY the bare factual claim on a single line, prefixed exactly '
                        '"CLAIM: " — do NOT phrase it as a question')


@dataclass(frozen=True)
class ArmSpec:
    name: str
    oracle_normalizer: str          # "strict" | "pilot"
    composer: str                   # "clean" | "pilot"
    done_detector: str              # "robust" | "pilot"
    placebo: bool = False
    randomize_ab_per_budget: bool = False   # LEGACY replay only
    parser_primary: str = "strict"          # LEGACY: "pilot"


ARMS = {
    "clean": ArmSpec("clean", "strict", "clean", "robust"),
    "both": ArmSpec("both", "pilot", "pilot", "pilot"),
    "placebo": ArmSpec("placebo", "strict", "clean", "robust", placebo=True),
    "na_only": ArmSpec("na_only", "pilot", "clean", "robust"),
    "doubled_only": ArmSpec("doubled_only", "strict", "pilot", "robust"),
    "legacy": ArmSpec("legacy", "pilot", "pilot", "pilot",
                      randomize_ab_per_budget=True, parser_primary="pilot"),
}

DEFAULT_BUDGETS = {
    "clean": [0, 1, 2, 5],
    "both": [1, 2, 5],
    "placebo": [1, 2, 5],
    "na_only": [1, 2],
    "doubled_only": [1, 2],
    "legacy": [1, 2],
}


def load_protocol(path: str | Path = DEFAULT_PROTOCOL_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_query_phase_prompt(pilot_prompt: str) -> str:
    """Rewrite the pilot's query-phrasing instruction to the CLEAN bare-claim form.

    Raises ValueError if the anchor line is missing (protocol drift guard).
    """
    if _PILOT_PHRASING_LINE not in pilot_prompt:
        raise ValueError("pilot query_phase_prompt anchor line not found — protocol drifted?")
    return pilot_prompt.replace(_PILOT_PHRASING_LINE, _CLEAN_PHRASING_LINE)


def make_seed(*parts) -> int:
    """Port of judge.py:make_seed."""
    key = "|".join(str(p) for p in parts)
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def judgment_seed(question_id, transcript_index, judge_model, budget, arm_name, replicate) -> int:
    return make_seed(question_id, transcript_index, judge_model, budget, arm_name, replicate)


def position_for(arm: ArmSpec, question_id, transcript_index, judge_model, budget) -> bool:
    if arm.randomize_ab_per_budget:
        seed = make_seed(question_id, transcript_index, judge_model, budget)
        return random.Random(seed).choice([True, False])
    return position_a_is_correct(question_id, transcript_index)
