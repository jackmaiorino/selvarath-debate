"""Durable per-call response cache, the prerequisite for the canary pause protocol.

The canary pauses a cell whenever a query payload has no committed reviewer decision, then
resumes it after the payload is labelled. Judge queries run at temperature 0.3, so a naive
resume re-runs the cell and gets *different* query text, which hashes to a different payload,
which is unlabelled, which pauses again. Every resume also re-spends the calls it repeats.

This cache breaks that loop. Each provider response is memoised against the call that produced
it, so a resumed cell replays its earlier calls verbatim and only the genuinely new call goes
live. Replay is only sound if the request is unchanged, so every entry binds a fingerprint of
the request and a mismatch halts rather than serving a response generated for a different
prompt.
"""
import json

import pytest

from rejudge.phase2_call_cache import (
    CacheIdentityMismatch, CallCache, CallKey, CallReplayMismatch, request_fingerprint)


CELL = "canary|sequential_b2|CN-001|judge|debater|0|0|2"


def _key(call_role="judge_query", slot=1, attempt=1, cell_key=CELL):
    return CallKey(cell_key=cell_key, call_role=call_role, slot=slot, attempt=attempt)


def _fp(text="hello"):
    return request_fingerprint(
        messages=[{"role": "user", "content": text}], model="m", temperature=0.3,
        seed=7, max_tokens=256)


# --- memoisation -----------------------------------------------------------------------

def test_a_stored_response_is_returned_for_the_same_call_and_request(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(), _fp(), "CLAIM: the council was established in Year 31")
    assert cache.get(_key(), _fp()) == "CLAIM: the council was established in Year 31"


def test_an_unknown_call_is_a_miss(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    assert cache.get(_key(), _fp()) is None


def test_slot_and_attempt_are_part_of_the_identity(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(slot=1, attempt=1), _fp(), "first")
    assert cache.get(_key(slot=1, attempt=2), _fp()) is None
    assert cache.get(_key(slot=2, attempt=1), _fp()) is None


def test_the_same_call_in_a_different_cell_is_a_miss(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(), _fp(), "first")
    assert cache.get(_key(cell_key="other|cell"), _fp()) is None


def test_a_response_is_stored_verbatim(tmp_path):
    # The frozen checker parser treats a trailing newline as malformed by design, so the
    # cache must not normalise what it replays.
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(call_role="query_checker"), _fp(), "allow\n")
    assert cache.get(_key(call_role="query_checker"), _fp()) == "allow\n"


# --- append-only ------------------------------------------------------------------------

def test_a_second_write_for_the_same_call_is_refused(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(), _fp(), "first")
    with pytest.raises(ValueError):
        cache.put(_key(), _fp(), "second")
    assert cache.get(_key(), _fp()) == "first"


# --- durability across process exit ------------------------------------------------------

def test_entries_survive_reopening(tmp_path):
    path = tmp_path / "calls.jsonl"
    CallCache(path).put(_key(), _fp(), "persisted")
    assert CallCache(path).get(_key(), _fp()) == "persisted"


def test_a_resumed_cell_replays_earlier_calls_and_leaves_the_new_one_live(tmp_path):
    path = tmp_path / "calls.jsonl"
    first_pass = CallCache(path)
    first_pass.put(_key(slot=1), _fp("q1"), "CLAIM: one")

    resumed = CallCache(path)
    assert resumed.get(_key(slot=1), _fp("q1")) == "CLAIM: one"   # replayed, no spend
    assert resumed.get(_key(slot=2), _fp("q2")) is None            # genuinely new, goes live


# --- replay fidelity ---------------------------------------------------------------------

def test_a_changed_request_for_a_cached_call_halts(tmp_path):
    # Serving a response generated for a different prompt would silently corrupt the run.
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(), _fp("original prompt"), "CLAIM: one")
    with pytest.raises(CallReplayMismatch) as excinfo:
        cache.get(_key(), _fp("edited prompt"))
    assert CELL in str(excinfo.value)


def test_the_fingerprint_covers_every_field_that_changes_the_response():
    def fingerprint(text="x", model="m", temperature=0.3, seed=7, max_tokens=256):
        return request_fingerprint(
            messages=[{"role": "user", "content": text}], model=model,
            temperature=temperature, seed=seed, max_tokens=max_tokens)

    variants = [
        fingerprint(text="y"),
        fingerprint(model="other"),
        fingerprint(temperature=0.0),
        fingerprint(seed=8),
        fingerprint(max_tokens=512),
    ]
    fingerprints = {fingerprint()} | set(variants)
    assert len(fingerprints) == len(variants) + 1


# --- tamper detection --------------------------------------------------------------------

def test_a_tampered_row_is_refused_on_load(tmp_path):
    path = tmp_path / "calls.jsonl"
    cache = CallCache(path)
    cache.put(_key(), _fp(), "original")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["response"] = "forged"
    path.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        CallCache(path)


def test_a_truncated_chain_is_refused_on_load(tmp_path):
    path = tmp_path / "calls.jsonl"
    cache = CallCache(path)
    cache.put(_key(slot=1), _fp(), "one")
    cache.put(_key(slot=2), _fp(), "two")
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[1] + "\n", encoding="utf-8")  # drop the first row
    with pytest.raises(ValueError):
        CallCache(path)


# --- execution identity --------------------------------------------------------------------

def test_a_cache_written_under_another_execution_identity_is_refused(tmp_path):
    # A cache hit bypasses the provider AND the spend ledger, so a stale cache from a prior
    # attempt would silently inject responses generated under a different manifest. The
    # request fingerprint cannot catch this: an identical request from an unrelated run
    # matches perfectly.
    path = tmp_path / "calls.jsonl"
    CallCache(path, execution_identity="aaa").put(_key(), _fp(), "from the first run")
    with pytest.raises(CacheIdentityMismatch) as excinfo:
        CallCache(path, execution_identity="bbb")
    assert "aaa" not in str(excinfo.value) or "bbb" in str(excinfo.value)


def test_a_cache_reopens_under_its_own_execution_identity(tmp_path):
    path = tmp_path / "calls.jsonl"
    CallCache(path, execution_identity="aaa").put(_key(), _fp(), "persisted")
    assert CallCache(path, execution_identity="aaa").get(_key(), _fp()) == "persisted"


def test_an_unidentified_cache_is_refused_by_an_identified_run(tmp_path):
    path = tmp_path / "calls.jsonl"
    CallCache(path).put(_key(), _fp(), "unbound")
    with pytest.raises(CacheIdentityMismatch):
        CallCache(path, execution_identity="aaa")


# --- accounting ---------------------------------------------------------------------------

def test_the_cache_reports_what_it_replayed(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    cache.put(_key(slot=1), _fp("a"), "one")
    cache.get(_key(slot=1), _fp("a"))
    cache.get(_key(slot=1), _fp("a"))
    cache.get(_key(slot=2), _fp("b"))
    assert cache.replayed == 2
    assert cache.missed == 1
