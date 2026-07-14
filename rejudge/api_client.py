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
        self._streaming_models = set()   # endpoints that reject non-streaming calls

    @property
    def spent_usd(self) -> float:
        return self.total_tokens / 1_000_000 * self.price_per_mtok

    def _streamed_create(self, model, messages, temperature, max_tokens, seed):
        """Call a streaming-only endpoint and reassemble a response-shaped object.

        Accumulates delta content across chunks; usage is taken from the final chunk
        (Together sends it there when stream_options requests it). Returns an object
        with .usage and .choices[0].message.content so the non-streaming accounting
        path applies unchanged.
        """
        stream = self._client().chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, seed=seed, stream=True)
        parts = []
        usage = None
        for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                text = getattr(delta, "content", None) if delta is not None else None
                if text:
                    parts.append(text)
        if usage is None:
            raise RuntimeError("streaming response ended without usage chunk")

        class _Msg:
            content = "".join(parts)

        class _Choice:
            message = _Msg()

        class _Resp:
            pass

        resp = _Resp()
        resp.usage = usage
        resp.choices = [_Choice()]
        return resp

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

    # Models whose replies arrive after a hidden reasoning phase that consumes output tokens.
    # A small max_tokens starves the visible answer entirely (observed: Qwen3.5-9B returned
    # empty content in 396/396 calls at max_tokens<=512 while billing ~90 tokens of reasoning).
    # For these endpoints the requested max_tokens is raised to a floor so the answer can
    # actually be emitted; the verdict/query parsers are unaffected (content stays clean).
    REASONING_MODEL_PREFIXES = ("Qwen/Qwen3.5", "Qwen/Qwen3.6", "Qwen/Qwen3.7",
                                "google/gemma-4", "openai/gpt-oss")
    REASONING_MAX_TOKENS_FLOOR = 4096

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict") -> str:
        if model.startswith(self.REASONING_MODEL_PREFIXES):
            max_tokens = max(max_tokens, self.REASONING_MAX_TOKENS_FLOOR)
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
                if model in self._streaming_models:
                    resp = self._streamed_create(model, messages, temperature, max_tokens, seed)
                else:
                    resp = self._client().chat.completions.create(
                        model=model, messages=messages, temperature=temperature,
                        max_tokens=max_tokens, seed=seed)
            except Exception as exc:                     # transient API error, no charge known
                if "streaming_required" in str(exc) or "supports streaming" in str(exc):
                    # streaming-only endpoint: remember and retry immediately via streaming
                    self._streaming_models.add(model)
                    last = exc
                    continue
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
