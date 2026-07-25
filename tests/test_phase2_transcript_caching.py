"""The caching client driven through the real debate_gen transcript path.

Transcript generation is the one canary stage whose calls are numbered by round and slot
rather than by query index, and it is the stage most likely to expose a key-derivation gap:
a three-round debate makes six calls into one cell, all with identical cell_key and call_role.
A derivation that misses round_index/slot_index collapses them onto a single cache entry.

Driving the genuine debate_gen code path rather than a hand-built metadata dict is the point:
these tests fail if debate_gen's metadata contract ever changes underneath the cache.
"""
from rejudge import config, debate_gen
from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient


QUESTION = {
    "id": "CN-001",
    "world": "selvarath",
    "question": "How many guilds hold council seats?",
    "correct_answer": "Seven guilds hold council seats.",
    "wrong_answer": "Nine guilds hold council seats.",
    "wrong_answer_defensibility": "The ninth and tenth seats were added provisionally.",
}


class CountingDebater:
    """Returns a distinct short turn per call, so a collision shows up as a wrong transcript."""

    def __init__(self):
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 **kwargs):
        self.calls.append(kwargs.get("request_metadata"))
        return f"Turn number {len(self.calls)}."


def _generate(client):
    return debate_gen.generate_transcript(
        QUESTION, "WORLD DOCUMENT", 0, True, config.load_protocol(), client,
        debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        protocol_name="uncapped3")


def test_a_three_round_debate_makes_six_distinct_cached_calls(tmp_path):
    inner = CountingDebater()
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    transcript = _generate(client)

    assert len(inner.calls) == 6
    texts = [turn["text"] for turn in transcript["debate_transcript"]]
    assert texts == [f"Turn number {i}." for i in range(1, 7)]
    assert len(set(texts)) == 6, "a cache collision would repeat a turn"


def test_regenerating_the_same_transcript_replays_every_turn(tmp_path):
    path = tmp_path / "calls.jsonl"
    first = _generate(CachingClient(CountingDebater(), CallCache(path)))

    replay_inner = CountingDebater()
    second = _generate(CachingClient(replay_inner, CallCache(path)))

    assert replay_inner.calls == [], "a fully cached transcript must make no provider calls"
    assert [t["text"] for t in second["debate_transcript"]] == [
        t["text"] for t in first["debate_transcript"]]


def test_a_capped_transcript_caches_its_word_cap_retries_separately(tmp_path):
    # The 150-word cap protocol re-asks an over-length turn with a stronger reminder, marking
    # the attempt as cap_regen_attempt. Those re-asks share cell_key, call_role, round and
    # slot with the original, so only the attempt widens the key.
    class Verbose(CountingDebater):
        def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                     **kwargs):
            self.calls.append(kwargs.get("request_metadata"))
            # Over the 150-word cap on the first attempt of each turn, under it after that.
            attempt = (kwargs.get("request_metadata") or {}).get("cap_regen_attempt")
            return "word " * 200 if attempt == 1 else "short turn"

    inner = Verbose()
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    debate_gen.generate_transcript(
        QUESTION, "WORLD DOCUMENT", 0, True, config.load_protocol(), client,
        debater_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        protocol_name="capped3")

    attempts = [(m.get("round_index"), m.get("slot_index"), m.get("cap_regen_attempt"))
                for m in inner.calls]
    assert len(attempts) == len(set(attempts)), "every retry must be its own cache entry"
    assert {a[2] for a in attempts} == {1, 2}, "one over-length attempt then one under"
