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
            return _DRY[kind]
        with self._lock:
            projected = (self.total_tokens + est) / 1_000_000 * self.price_per_mtok
            if projected > self.approved_cap_usd:
                raise CapExceededError(
                    f"projected spend ${projected:.4f} > approved cap ${self.approved_cap_usd:.4f}")
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client().chat.completions.create(
                    model=model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, seed=seed)
                with self._lock:
                    self.total_tokens += (resp.usage.prompt_tokens + resp.usage.completion_tokens)
                content = resp.choices[0].message.content
                return content if content is not None else ""
            except (CapExceededError, ContextGuardError):
                raise
            except Exception as exc:                     # transient API error
                last = exc
                self._log_error(attempt, model, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"API call failed after {self.max_retries + 1} attempts: {last}")
