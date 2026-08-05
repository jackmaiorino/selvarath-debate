"""Client wrapper that puts the per-call cache in front of the provider.

The canary runner drives the ordinary ``complete()`` interface through debate_gen, judge_loop
and the gate, so the cache is applied by wrapping the client rather than by threading a cache
argument through all three. A cached call returns without reaching the provider, so it never
touches the spend ledger: that is what makes a resumed cell free.

Every call must be attributable to a cell and a role. An uncacheable call in a resumable run
is a silent double-spend on every resume, so a call that cannot be keyed is refused outright
rather than quietly passed through.
"""
from __future__ import annotations

from rejudge.phase2_call_cache import CallCache, CallKey, request_fingerprint


class UncacheableCall(RuntimeError):
    """Raised when a call carries too little metadata to be cached, and so to be resumed."""


def _derive_slot(metadata) -> int:
    """Locate the call within its cell.

    Each call site numbers its calls differently, and none of them agree:

    - the canary gate counts query slots explicitly as ``slot``;
    - ``judge_loop`` counts them as ``query_index``, for both the query and its oracle call;
    - ``debate_gen`` identifies a debater turn by ``round_index`` and ``slot_index`` and sets
      neither of the above, so without this branch every turn of a debate would collide on
      slot 0;
    - single-call roles (verdicts) carry none of them and correctly land on 0.

    The round/slot fold matches debate_gen's own turn ordering (``round_idx * 2 + slot``).
    """
    if "slot" in metadata:
        return int(metadata["slot"])
    if "query_index" in metadata:
        return int(metadata["query_index"])
    if "round_index" in metadata and "slot_index" in metadata:
        return int(metadata["round_index"]) * 2 + int(metadata["slot_index"])
    return 0


def _derive_attempt(metadata) -> int:
    """Distinguish retries of the same call.

    ``attempt`` is the gated judge-query retry; ``cap_regen_attempt`` is debate_gen's
    word-cap regeneration. They never co-occur, and each re-asks the same logical call with a
    different prompt, so both must widen the key rather than overwrite the earlier entry.
    """
    for field in ("attempt", "cap_regen_attempt"):
        if field in metadata:
            return int(metadata[field])
    return 1


class CachingClient:
    """Memoises provider responses per (cell, role, slot, attempt)."""

    def __init__(self, inner, cache: CallCache) -> None:
        self.inner = inner
        self.cache = cache

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    @staticmethod
    def _key(request_metadata) -> CallKey:
        if not request_metadata:
            raise UncacheableCall(
                "request_metadata is required: an uncacheable call cannot be replayed on "
                "resume, and would be re-spent on every pause")
        cell_key = request_metadata.get("cell_key")
        call_role = request_metadata.get("call_role")
        if not cell_key or not call_role:
            raise UncacheableCall(
                "request_metadata must carry both cell_key and call_role; got "
                f"cell_key={cell_key!r}, call_role={call_role!r}")
        return CallKey(cell_key=str(cell_key), call_role=str(call_role),
                       slot=_derive_slot(request_metadata),
                       attempt=_derive_attempt(request_metadata))

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None) -> str:
        key = self._key(request_metadata)
        fingerprint = request_fingerprint(
            messages=messages, model=model, temperature=temperature, seed=seed,
            max_tokens=max_tokens)
        cached = self.cache.get(key, fingerprint)
        if cached is not None:
            return cached
        # Committed only after the provider returns: a failed call must stay uncached so a
        # later attempt can go live rather than replaying a response that never existed.
        response = self.inner.complete(
            messages, model, temperature, seed, max_tokens, kind=kind,
            request_metadata=request_metadata)
        # An empty response is not a slow or unusual answer, it is the absence of one, and the
        # cache exists to replay answers. Memoising it converts a transient into a permanent:
        # the 2026-07-28 canary lost four cells this way and the bridge canary three more, one
        # per ~199 cells, because every resume replayed the same unreadable response and the
        # cell could never complete. The same rate over the main run is ~116 unrecoverable
        # cells.
        #
        # Re-calling is sound rather than a retry-until-favourable loop, and specifically
        # because of what is NOT cached here. The frozen checker runs at temperature 0, so a
        # successful call is deterministic: a retry recovers the decision the failed call
        # should have returned rather than drawing a fresh sample. The frozen
        # "never regenerate an invalid verdict" rule guards sampled outputs against exactly
        # that selection pressure, and an empty string is not a sampled output.
        if response.strip():
            self.cache.put(key, fingerprint, response)
        return response
