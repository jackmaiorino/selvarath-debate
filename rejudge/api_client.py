"""Together client wrapper: retries/backoff, cost cap, context guard, dry-run tagging.

The real SDK is imported lazily and only when needed, so tests never touch it.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone


class CapExceededError(RuntimeError):
    pass


class ContextGuardError(RuntimeError):
    pass


_DRY = {
    "query": "CLAIM: [DRY RUN] the sky over the capital is described as blue",
    "oracle": "YES [DRY RUN]",
    "verdict": "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: [DRY RUN] synthetic response.",
}


def _estimate_tokens(messages, max_tokens):
    return sum(len(m["content"]) for m in messages) // 4 + max_tokens


class RejudgeClient:
    def __init__(self, approved_cap_usd, price_per_mtok=1.04, dry_run=False,
                 error_log_path=None, max_context_tokens=131072, max_retries=4,
                 _sdk_client=None, _sleep=time.sleep):
        self.approved_cap_usd = approved_cap_usd
        self.price_per_mtok = price_per_mtok
        self.dry_run = dry_run
        self.error_log_path = error_log_path
        self.max_context_tokens = max_context_tokens
        self.max_retries = max_retries
        self._sdk = _sdk_client
        self._sleep = _sleep
        self._lock = threading.Lock()
        self.total_tokens = 0

    @property
    def spent_usd(self) -> float:
        return self.total_tokens / 1_000_000 * self.price_per_mtok

    def _log_error(self, attempt, model, exc):
        if not self.error_log_path:
            return
        with self._lock:
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                    "attempt": attempt, "model": model,
                                    "error": str(exc)}) + "\n")

    def _client(self):
        if self._sdk is None:
            from together import Together
            self._sdk = Together()
        return self._sdk

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict") -> str:
        est = _estimate_tokens(messages, max_tokens)
        if est > self.max_context_tokens:
            raise ContextGuardError(f"estimated {est} tokens > {self.max_context_tokens}")
        if self.dry_run:
            if kind not in _DRY:
                raise ValueError(f"unknown kind: {kind!r}")
            return _DRY[kind]
        # Check-and-reserve is one atomic critical section: a concurrent caller that acquires
        # the lock right after us will see our reservation already counted in total_tokens, so
        # two callers can never both pass the projection check for spend the cap can't cover.
        with self._lock:
            projected = (self.total_tokens + est) / 1_000_000 * self.price_per_mtok
            if projected > self.approved_cap_usd:
                raise CapExceededError(
                    f"projected spend ${projected:.4f} > approved cap ${self.approved_cap_usd:.4f}")
            self.total_tokens += est   # reserve the estimate up front
        last = None
        # Reconciliation is NOT guaranteed exactly-once per call: it happens as soon as we
        # know the actual usage (see the malformed-response branch below, which reconciles
        # then raises immediately rather than retrying). And CapExceededError CAN now be
        # raised from inside this loop -- see the re-check below -- unlike ContextGuardError,
        # which is only ever raised above, before any reservation exists.
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                # Re-check the cap before every RETRY (not just the one up-front reservation
                # check above). Scheme: keep the single up-front reservation as-is (it is not
                # touched here, so it can't be double-counted); instead check *actual*
                # accounted spend (self.total_tokens, which by now may include real usage
                # reconciled by this call or -- since total_tokens is shared -- by a
                # concurrent call on the same client) against the cap. If accounted spend has
                # already crossed the cap, refuse to pay for another attempt: raise
                # CapExceededError instead of retrying into a generic RuntimeError once
                # attempts are exhausted (the original bug: retries kept firing paid calls
                # with no cap re-check, blowing through the cap by 66%).
                with self._lock:
                    spent = self.total_tokens / 1_000_000 * self.price_per_mtok
                if spent > self.approved_cap_usd:
                    raise CapExceededError(
                        f"accounted spend ${spent:.4f} > approved cap ${self.approved_cap_usd:.4f} "
                        f"after {attempt} attempt(s); refusing to retry")
            try:
                resp = self._client().chat.completions.create(
                    model=model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, seed=seed)
            except Exception as exc:                     # transient API error, no charge known
                last = exc
                self._log_error(attempt, model, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                continue
            # The call above returned -- it may have been billed. Read usage and content into
            # locals BEFORE reconciling: reconciling early (the original bug) meant a later
            # exception mid-attempt (e.g. malformed choices) fell into the generic retry
            # branch and re-reconciled on every subsequent retry against the same stale `est`,
            # blowing through the cap with no re-check.
            try:
                actual = resp.usage.prompt_tokens + resp.usage.completion_tokens
            except Exception as exc:          # usage itself unreadable -- unknown charge
                last = exc                    # nothing safe to reconcile; treat as transient
                self._log_error(attempt, model, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                continue
            try:
                content = resp.choices[0].message.content
            except Exception as exc:
                # Usage was readable, so a real, paid call happened -- reconcile the actual
                # spend now (it must not be lost or left rolled back) and stop: this is
                # TERMINAL, not retried. Retrying a malformed-response condition would pay
                # for another call with no reason to expect a different shape back.
                with self._lock:
                    self.total_tokens += actual - est
                raise RuntimeError(
                    f"malformed API response after successful charge: {exc}") from exc
            with self._lock:
                self.total_tokens += actual - est   # reconcile reservation -> actual usage
            return content if content is not None else ""
        with self._lock:
            self.total_tokens -= est   # terminal failure: release the reservation, never spent
        raise RuntimeError(f"API call failed after {self.max_retries + 1} attempts: {last}")
