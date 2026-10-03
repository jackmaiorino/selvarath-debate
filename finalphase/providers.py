"""Provider adapters: one request/response shape over OpenAI, Anthropic and Together.

Live calls go through the official SDKs. Batch calls exist for OpenAI and Anthropic.
Nothing here decides retries on measured failures: a refusal, truncation or empty
answer comes back as a Response with that status and is scored by the caller.
Transport errors raise TransportError and are retried by the runner.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ModelSpec, cost_usd


class TransportError(RuntimeError):
    """Retryable failure: network, 429, 5xx, provider overload, expired batch item."""


class BillingError(RuntimeError):
    """Account-level failure (no credits, bad key). Halts the run; never retried or scored."""


_BILLING_MARKERS = ("insufficient_quota", "credit_balance_exhausted", "billing", "credit balance is too low")


def _billing_check(provider: str, e: Exception) -> None:
    text = str(e).lower()
    status = getattr(e, "status_code", None)
    if status in (401, 403) or any(k in text for k in _BILLING_MARKERS):
        raise BillingError(f"{provider}: {e}"[:1000]) from e


@dataclass(frozen=True)
class Request:
    custom_id: str
    model: str  # registry key
    system: str
    messages: tuple[tuple[str, str], ...]  # (role, content); roles "user" / "assistant"
    max_tokens: int
    effort: str | None = None  # overrides the model's default effort for this role
    cache_first: int = 0  # Anthropic: mark the first N chars of messages[0] as a cache breakpoint (-1 = whole message)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)


@dataclass
class Response:
    custom_id: str
    model_id: str
    status: str  # "ok" | "truncated" | "refusal" | "error"
    text: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    batch: bool = False
    provider_id: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def batch_request_id(custom_id: str, provider: str) -> str:
    """Keep study IDs in the store and use valid IDs on Anthropic's batch API."""
    return hashlib.sha256(custom_id.encode("utf-8")).hexdigest() if provider == "anthropic" else custom_id


# ---------------------------------------------------------------- OpenAI

def _openai_client():
    import openai

    return openai.OpenAI(max_retries=0, timeout=900)


def _openai_body(req: Request, m: ModelSpec) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": m.model_id,
        "instructions": req.system,
        "input": [{"role": r, "content": c} for r, c in req.messages],
        "max_output_tokens": req.max_tokens,
        "store": False,
    }
    if req.effort or m.effort:
        body["reasoning"] = {"effort": req.effort or m.effort}
    return body


def _openai_parse(custom_id: str, m: ModelSpec, body: dict[str, Any], batch: bool) -> Response:
    text = body.get("output_text")
    if text is None:
        parts = []
        for item in body.get("output") or []:
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "refusal":
                        return _finish(Response(custom_id, m.model_id, "refusal", c.get("refusal", "")), m, body, batch)
        text = "".join(parts)
    status = "ok"
    detail = ""
    if body.get("status") == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason", "")
        status = "truncated" if reason == "max_output_tokens" else "refusal" if reason == "content_filter" else "error"
        detail = reason
    elif body.get("status") not in (None, "completed"):
        status, detail = "error", str(body.get("status"))
    return _finish(Response(custom_id, m.model_id, status, text, detail=detail), m, body, batch)


def _finish(r: Response, m: ModelSpec, body: dict[str, Any], batch: bool) -> Response:
    u = body.get("usage") or {}
    r.input_tokens = int(u.get("input_tokens") or 0)
    r.cached_tokens = int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    r.output_tokens = int(u.get("output_tokens") or 0)
    r.reasoning_tokens = int((u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
    r.provider_id = str(body.get("id", ""))
    r.batch = batch
    r.cost = cost_usd(m, r.input_tokens, r.output_tokens, r.cached_tokens, batch)
    return r


def openai_live(req: Request, m: ModelSpec) -> Response:
    import openai

    try:
        resp = _openai_client().responses.create(**_openai_body(req, m))
    except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError) as e:
        _billing_check("openai", e)
        raise TransportError(f"openai {type(e).__name__}: {e}") from e
    except openai.APIStatusError as e:
        _billing_check("openai-compatible", e)
        if e.status_code >= 500 or e.status_code in (408, 409, 429):
            raise TransportError(f"openai {e.status_code}: {e}") from e
        return Response(req.custom_id, m.model_id, "error", "", detail=f"{e.status_code}: {e}"[:2000])
    return _openai_parse(req.custom_id, m, resp.model_dump(), batch=False)


def openai_batch_submit(reqs: list[Request], m: ModelSpec, label: str) -> str:
    client = _openai_client()
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for r in reqs:
            f.write(json.dumps({"custom_id": r.custom_id, "method": "POST", "url": "/v1/responses", "body": _openai_body(r, m)}) + "\n")
        path = f.name
    with open(path, "rb") as fh:
        up = client.files.create(file=fh, purpose="batch")
    os.unlink(path)
    b = client.batches.create(input_file_id=up.id, endpoint="/v1/responses", completion_window="24h", metadata={"label": label[:500]})
    return b.id


def openai_batch_poll(batch_id: str) -> tuple[str, dict[str, Any]]:
    b = _openai_client().batches.retrieve(batch_id)
    done = b.status in ("completed", "failed", "expired", "cancelled")
    return ("ended" if done else b.status), b.model_dump()


def openai_batch_collect(batch_id: str, m: ModelSpec) -> dict[str, Response | TransportError]:
    client = _openai_client()
    b = client.batches.retrieve(batch_id)
    out: dict[str, Response | TransportError] = {}
    for fid in (b.output_file_id, b.error_file_id):
        if not fid:
            continue
        for line in client.files.content(fid).text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row["custom_id"]
            resp = row.get("response") or {}
            code = resp.get("status_code")
            if code == 200:
                out[cid] = _openai_parse(cid, m, resp["body"], batch=True)
            elif code is not None and code < 500 and code not in (408, 409, 429):
                out[cid] = Response(cid, m.model_id, "error", "", batch=True, detail=json.dumps(resp.get("body"))[:2000])
            else:
                out[cid] = TransportError(f"batch item {code}: {json.dumps(row.get('error') or resp)[:500]}")
    return out


# ---------------------------------------------------------------- Anthropic

def _anthropic_client():
    import anthropic

    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return anthropic.Anthropic(max_retries=0, timeout=900, default_headers=headers)


def _anthropic_params(req: Request, m: ModelSpec) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = [{"role": r, "content": c} for r, c in req.messages]
    if req.cache_first and msgs:
        first = msgs[0]["content"]
        n = len(first) if req.cache_first < 0 else min(req.cache_first, len(first))
        blocks = [{"type": "text", "text": first[:n], "cache_control": {"type": "ephemeral"}}]
        if first[n:]:
            blocks.append({"type": "text", "text": first[n:]})
        msgs[0] = {"role": msgs[0]["role"], "content": blocks}
    p: dict[str, Any] = {
        "model": m.model_id,
        "max_tokens": req.max_tokens,
        "system": req.system,
        "messages": msgs,
    }
    if req.effort or m.effort:
        p["output_config"] = {"effort": req.effort or m.effort}
    return p


def _anthropic_parse(custom_id: str, m: ModelSpec, msg: dict[str, Any], batch: bool) -> Response:
    text = "".join(b.get("text", "") for b in msg.get("content") or [] if b.get("type") == "text")
    stop = msg.get("stop_reason")
    status = {"end_turn": "ok", "stop_sequence": "ok", "max_tokens": "truncated", "refusal": "refusal"}.get(stop, "error")
    detail = "" if status == "ok" else f"stop_reason={stop} {json.dumps(msg.get('stop_details'))}"
    u = msg.get("usage") or {}
    cached = int(u.get("cache_read_input_tokens") or 0)
    created = int(u.get("cache_creation_input_tokens") or 0)
    inp = int(u.get("input_tokens") or 0) + cached + created
    r = Response(custom_id, m.model_id, status, text, input_tokens=inp, cached_tokens=cached,
                 output_tokens=int(u.get("output_tokens") or 0), batch=batch, provider_id=str(msg.get("id", "")), detail=detail)
    r.cost = cost_usd(m, inp, r.output_tokens, cached, batch) + created * m.price_in * 0.25 / 1e6 * (0.5 if batch else 1.0)
    r.extra = {"cache_creation_tokens": created}
    return r


def anthropic_live(req: Request, m: ModelSpec) -> Response:
    import anthropic

    try:
        with _anthropic_client().messages.stream(**_anthropic_params(req, m)) as s:
            msg = s.get_final_message()
    except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
        _billing_check("anthropic", e)
        raise TransportError(f"anthropic {type(e).__name__}: {e}") from e
    except anthropic.APIStatusError as e:
        _billing_check("anthropic", e)
        if e.status_code >= 500 or e.status_code in (408, 409, 429, 529):
            raise TransportError(f"anthropic {e.status_code}: {e}") from e
        return Response(req.custom_id, m.model_id, "error", "", detail=f"{e.status_code}: {e}"[:2000])
    return _anthropic_parse(req.custom_id, m, msg.model_dump(), batch=False)


def anthropic_batch_submit(reqs: list[Request], m: ModelSpec, label: str) -> str:
    b = _anthropic_client().messages.batches.create(
        requests=[{"custom_id": batch_request_id(r.custom_id, "anthropic"), "params": _anthropic_params(r, m)} for r in reqs]
    )
    return b.id


def anthropic_batch_poll(batch_id: str) -> tuple[str, dict[str, Any]]:
    b = _anthropic_client().messages.batches.retrieve(batch_id)
    return b.processing_status, b.model_dump(mode="json")


def anthropic_batch_collect(batch_id: str, m: ModelSpec) -> dict[str, Response | TransportError]:
    out: dict[str, Response | TransportError] = {}
    with _anthropic_client() as client:
        for res in client.messages.batches.results(batch_id):
            r = res.result
            if r.type == "succeeded":
                out[res.custom_id] = _anthropic_parse(res.custom_id, m, r.message.model_dump(), batch=True)
            elif r.type == "errored" and getattr(r.error.error, "type", "") == "invalid_request_error":
                out[res.custom_id] = Response(res.custom_id, m.model_id, "error", "", batch=True, detail=str(r.error)[:2000])
            else:
                out[res.custom_id] = TransportError(f"batch item {r.type}")
    return out


# ---------------------------------------------------------------- Together

def together_live(req: Request, m: ModelSpec) -> Response:
    import openai

    client = openai.OpenAI(base_url="https://api.together.xyz/v1", api_key=os.environ["TOGETHER_API_KEY"], max_retries=0, timeout=900)
    msgs = [{"role": "system", "content": req.system}] + [{"role": r, "content": c} for r, c in req.messages]
    try:
        resp = client.chat.completions.create(model=m.model_id, messages=msgs, max_tokens=req.max_tokens)
    except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError) as e:
        _billing_check("together", e)
        raise TransportError(f"together {type(e).__name__}: {e}") from e
    except openai.APIStatusError as e:
        _billing_check("openai-compatible", e)
        if e.status_code >= 500 or e.status_code in (408, 409, 429):
            raise TransportError(f"together {e.status_code}: {e}") from e
        return Response(req.custom_id, m.model_id, "error", "", detail=f"{e.status_code}: {e}"[:2000])
    body = resp.model_dump()
    ch = (body.get("choices") or [{}])[0]
    text = (ch.get("message") or {}).get("content") or ""
    fin = ch.get("finish_reason")
    status = "ok" if fin in ("stop", "eos", None) else "truncated" if fin == "length" else "error"
    u = body.get("usage") or {}
    r = Response(req.custom_id, m.model_id, status, text, input_tokens=int(u.get("prompt_tokens") or 0),
                 output_tokens=int(u.get("completion_tokens") or 0),
                 reasoning_tokens=int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                 provider_id=str(body.get("id", "")),
                 detail="" if status == "ok" else f"finish_reason={fin}")
    r.cost = cost_usd(m, r.input_tokens, r.output_tokens)
    return r


LIVE = {"openai": openai_live, "anthropic": anthropic_live, "together": together_live}
BATCH = {
    "openai": (openai_batch_submit, openai_batch_poll, openai_batch_collect),
    "anthropic": (anthropic_batch_submit, anthropic_batch_poll, anthropic_batch_collect),
}
