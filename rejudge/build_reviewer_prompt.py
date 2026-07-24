"""Materialize the frozen Claude-gate reviewer prompt (amendment canary_claude_gate_2026-07-23).

The reviewer prompt = the frozen contract text + the unified codebook rules (the
v4 checker rule text, which IS the codebook's operative statement) + reviewer
output instructions (three-way label + controlling clause + one-sentence
rationale, contract compliance only). Deterministic: byte-identical output on
rerun; the sha256 is bound into the execution manifest.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rejudge.phase2_checker_dev_runner import VARIANTS  # noqa: E402

contract = (ROOT / "docs" / "oracle-query-contract.md").read_text(encoding="utf-8")
codebook = VARIANTS["v4_merged"]

REVIEWER_PROMPT = (
    "You are a blinded contract reviewer for an oracle-query gate in a debate "
    "experiment. You will be shown ONE query payload: a proposed query plus the two "
    "candidate answers it relates to. You know nothing else about where it came from, "
    "and you must not speculate about arms, modes, judges, or outcomes.\n\n"
    "Judge CONTRACT COMPLIANCE ONLY: never truth, usefulness, or what the answer might "
    "do to the debate.\n\n"
    "=== THE CONTRACT (verbatim) ===\n"
    + contract
    + "\n=== THE OPERATIVE CODEBOOK (apply these tests exactly) ===\n"
    + codebook
    + "\n\n=== YOUR OUTPUT ===\n"
    "Respond with exactly three lines:\n"
    "LABEL: ALLOW or REJECT or CONTRACT_AMBIGUOUS\n"
    "CLAUSE: Allowed or P1 or P2 or P3 or P4 (the controlling clause)\n"
    "RATIONALE: one sentence.\n"
    "CONTRACT_AMBIGUOUS is only for queries the contract and codebook genuinely do not "
    "decide. Note: the codebook text above ends by demanding a one-token reply; that "
    "instruction is for a different role and does NOT apply to you - your output is the "
    "three lines specified here."
)

out = {
    "schema_version": "phase2_reviewer_prompt_v1",
    "frozen_at_utc": "2026-07-23",
    "amendment": "rejudge/phase2_canary_claude_gate_amendment_2026-07-23.json",
    "reviewer_model": "claude-fable-5 in a fresh isolated context per batch (no repository, tools, memory, or cross-query discussion)",
    "sources": {
        "contract_sha256": hashlib.sha256(contract.encode("utf-8")).hexdigest(),
        "codebook_v4_sha256": hashlib.sha256(codebook.encode("utf-8")).hexdigest(),
    },
    "prompt": REVIEWER_PROMPT,
    "prompt_sha256": hashlib.sha256(REVIEWER_PROMPT.encode("utf-8")).hexdigest(),
    "payload_format": "QUERY: <raw_query>\\nCANDIDATE A: <candidate_a>\\nCANDIDATE B: <candidate_b>",
    "failure_rule": "any output not parseable as the three lines, or any timeout or reviewer unavailability, is committed as non-ALLOW",
}
path = ROOT / "rejudge" / "phase2_reviewer_prompt_2026-07-23.json"
if path.exists():
    existing = json.loads(path.read_text(encoding="utf-8"))
    assert existing["prompt_sha256"] == out["prompt_sha256"], "reviewer prompt drifted"
    print("unchanged:", out["prompt_sha256"])
else:
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("frozen:", out["prompt_sha256"])
    print("wrote", path)
