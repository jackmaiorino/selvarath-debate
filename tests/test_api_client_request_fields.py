import json
from pathlib import Path

from rejudge import api_client as ac
from rejudge import phase3_main_provider_provenance as provider_provenance


MESSAGES = [
    {"role": "system", "content": "policy"},
    {"role": "user", "content": "hello"},
]
QWEN_MODEL = "Qwen/Qwen3.8-2.4T-A95B"


def test_pure_provider_request_kwargs_and_hash_match_literal_payload():
    kwargs = provider_provenance.build_provider_request_kwargs(
        model=QWEN_MODEL,
        messages=MESSAGES,
        temperature=0.3,
        max_tokens=4096,
        seed=17,
        streaming=True,
        extra_request_fields={"reasoning_effort": "medium"},
    )

    assert kwargs == {
        "model": QWEN_MODEL,
        "messages": MESSAGES,
        "temperature": 0.3,
        "max_tokens": 4096,
        "seed": 17,
        "stream": True,
        "reasoning_effort": "medium",
    }
    assert "stream_options" not in kwargs
    assert provider_provenance.compute_request_fields_sha256(kwargs) == (
        "5b339782e08f33be536917bf39eaf328bcb3c3cab354857bfdf5118c123c71a2"
    )

    client = ac.RejudgeClient(
        approved_cap_usd=1.0,
        dry_run=True,
        extra_request_fields={QWEN_MODEL: {"reasoning_effort": "medium"}},
    )
    assert client._build_request_kwargs(
        model=QWEN_MODEL,
        messages=MESSAGES,
        temperature=0.3,
        max_tokens=4096,
        seed=17,
        streaming=True,
    ) == kwargs


def test_provider_hash_uses_post_role_limit_effective_max_tokens():
    role_limits_path = Path(ac.__file__).with_name(
        "phase3_v3_role_limits_r10_2026-08-28.json"
    )
    role_limits = json.loads(role_limits_path.read_text(encoding="utf-8"))
    query_limits = role_limits["model_role_limits"][QWEN_MODEL]["judge_query"]
    assert query_limits == {
        "base_role_max_tokens": 256,
        "effective_request_max_tokens": 4096,
    }

    base_kwargs = provider_provenance.build_provider_request_kwargs(
        model=QWEN_MODEL,
        messages=MESSAGES,
        temperature=0.3,
        max_tokens=query_limits["base_role_max_tokens"],
        seed=17,
        streaming=False,
    )
    wire_kwargs = provider_provenance.build_provider_request_kwargs(
        model=QWEN_MODEL,
        messages=MESSAGES,
        temperature=0.3,
        max_tokens=query_limits["effective_request_max_tokens"],
        seed=17,
        streaming=False,
    )

    assert base_kwargs["max_tokens"] == 256
    assert wire_kwargs["max_tokens"] == 4096
    assert "stream" not in wire_kwargs
    assert provider_provenance.compute_request_fields_sha256(base_kwargs) == (
        "f755fe0b914f8d6d1fb39451b931d3737eb851efa59c6071b0530956205e9cf1"
    )
    assert provider_provenance.compute_request_fields_sha256(wire_kwargs) == (
        "2f32c5df7b6e6fc50487f29d65585aba87db9ce54edf732f069a9a94249e046a"
    )
    assert (
        provider_provenance.compute_request_fields_sha256(base_kwargs)
        != provider_provenance.compute_request_fields_sha256(wire_kwargs)
    )
