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
        # Slot numbering differs by call site: the gate counts slots explicitly, the judge
        # loop counts query_index, and single-call roles carry neither.
        slot = request_metadata.get("slot", request_metadata.get("query_index", 0))
        return CallKey(cell_key=str(cell_key), call_role=str(call_role), slot=int(slot),
                       attempt=int(request_metadata.get("attempt", 1)))

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
        self.cache.put(key, fingerprint, response)
        return response
