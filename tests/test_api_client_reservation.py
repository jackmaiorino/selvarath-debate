"""Reserving enough for reasoning tokens.

The reservation assumed completion <= max_tokens. For a reasoning model that is false: the
provider bills reasoning as completion and does not bound it by max_tokens. The 2026-08-06
main run halted on exactly that, at ledger event 17022 after 2,573 cells: 5,666 completion
tokens against a 4,096 allowance, with finish_reason "stop" rather than "length".

Over the run so far, 72 of 2,772 Qwen3.7-Plus calls exceeded 4,096 completion tokens,
reaching 6,753. Every other model's maximum stayed under it. Most of those 72 stayed inside
their reservation only because the byte-based prompt estimate over-reserves heavily (7,476
reserved against an actual 1,576); this call had a short prompt, so that padding was absent.
"""
from rejudge.api_client import (COMPLETION_RESERVE_MULTIPLIER, _estimate_usage,
                                reserved_completion_tokens)


def test_the_reservation_allows_for_reasoning_beyond_max_tokens():
    assert COMPLETION_RESERVE_MULTIPLIER >= 2
    assert reserved_completion_tokens(4096) >= 6753, (
        "must cover the largest completion actually observed in the live run")


def test_the_allowance_scales_with_max_tokens():
    assert reserved_completion_tokens(512) == 512 * COMPLETION_RESERVE_MULTIPLIER
    assert reserved_completion_tokens(4096) == 4096 * COMPLETION_RESERVE_MULTIPLIER


def test_the_context_guard_estimate_is_unaffected():
    """The same estimate feeds the context-ceiling check. Inflating THAT would refuse calls
    that fit perfectly well, trading an accounting halt for a spurious guard halt."""
    messages = [{"role": "user", "content": "x" * 4000}]
    _prompt, completion = _estimate_usage(messages, 4096)
    assert completion == 4096, "the context estimate must stay the true max_tokens"
    assert reserved_completion_tokens(completion) > completion


def test_the_reservation_and_its_terminal_event_agree_on_estimated_tokens():
    """The ledger pins estimated_tokens as a STABLE field across a reservation and its
    terminal event: they must describe the same attempt. Raising the reservation's completion
    allowance without raising the terminal event's made every reasoning-model call disagree
    with its own reservation, and the ledger refused at event 40.

    One value, computed once, used for both. The context-ceiling check keeps the true
    estimate, since that is a question about what fits, not about what to reserve.
    """
    import inspect

    from rejudge import api_client

    src = inspect.getsource(api_client.RejudgeClient.complete)
    assert "reserved_tokens" in src, (
        "the reserved total must be a named value used for both the reservation and the "
        "terminal events, not recomputed differently in each place")


def test_reserved_tokens_exceed_the_context_estimate_for_reasoning_models():
    from rejudge.api_client import reserved_completion_tokens
    prompt, completion = 1000, 4096
    context_total = prompt + completion
    reserved_total = prompt + reserved_completion_tokens(completion)
    assert reserved_total > context_total
