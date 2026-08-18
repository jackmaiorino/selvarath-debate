"""Phase-3 engineering gates 5-7: the 2026-08-10 five-hour ssl.read hang.

Section 1 drives ``rejudge.api_client``'s REAL transport (the actual ``together``/``httpx``
stack, no mocks) against a local, no-TLS socket server that accepts a connection, sends valid
response headers plus a few body bytes, and then never sends another byte -- the fault the
2026-08-10 main run hit. It proves the configured read timeout DOES fire at the socket level,
across the non-streaming path, the streaming (SSE) path, and a reused pooled connection, once
the v5 transport pins actually reach the live client. It also pins the concrete gap that
explains why they did not reach it on 2026-08-10: ``rejudge.run_accounting.create_accounted_client``
(the factory ``rejudge/runner.py`` -- the main-run driver -- actually calls) never threaded
``http_timeout`` through at all, so that run's live client fell back to the Together SDK's own
unpinned default rather than the role-limits v6 pins.

Section 2 unit-tests the new proactive per-call deadline watchdog
(``RejudgeClient._transport_deadline_seconds`` / ``_call_with_deadline``,
``TransportDeadlineExceeded``) against stub SDKs that block forever, independent of real
sockets: defense-in-depth for a call that never returns for ANY reason, not only the one this
fault injection reproduces.

Every test that could otherwise hang forever if a guarantee turns out NOT to hold runs the
blocking call on a background daemon thread and joins it with a generous, bounded timeout, so a
gap in the guarantee fails the test loudly instead of hanging the suite.
"""
from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import together

from rejudge import api_client as ac
from rejudge import run_accounting

MSGS = [{"role": "user", "content": "hello"}]


# --- Section 1: real-socket fault injection -------------------------------------------------


class _FaultInjectionServer:
    """A minimal HTTP/1.1 server, no TLS, for read-timeout fault injection.

    Answers the first ``healthy_requests`` requests on a connection with a complete, valid
    JSON chat-completion response (so httpx/httpcore genuinely keep the connection alive and
    reuse it); on the request after that it sends valid response headers plus a few body
    bytes and then holds the connection open forever without sending another byte -- headers
    arrive, the body never finishes, exactly the 2026-08-10 shape. No TLS: RejudgeClient's
    real Together SDK client accepts a plain-http base_url exactly as readily, so this drives
    the production transport code path (the same httpx.Client, the same connection pool, the
    same streaming/non-streaming branches in ``RejudgeClient.complete``) without a
    certificate.
    """

    _CHAT_BODY = (
        b'{"id":"fixture","object":"chat.completion","model":"m",'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"OK"},'
        b'"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
    )
    _SSE_FIRST_CHUNK = (
        b'data: {"id":"fixture","object":"chat.completion.chunk","model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    )

    def __init__(self, *, healthy_requests: int = 0, stall_body: str = "plain") -> None:
        self.healthy_requests = healthy_requests
        self.stall_body = stall_body            # "plain" (Content-Length) or "sse" (chunked-free)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._conns: list[socket.socket] = []
        self._conns_lock = threading.Lock()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._conns_lock:
                self._conns.append(conn)
            threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()

    @staticmethod
    def _read_one_request(conn: socket.socket) -> bool:
        """Read one full HTTP request off ``conn``. Returns False on EOF/closed."""
        conn.settimeout(5.0)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return False
            buf += chunk
        header_blob, _sep, rest = buf.partition(b"\r\n\r\n")
        content_length = 0
        for line in header_blob.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
        remaining = content_length - len(rest)
        while remaining > 0:
            chunk = conn.recv(min(65536, remaining))
            if not chunk:
                return False
            remaining -= len(chunk)
        return True

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            request_index = 0
            while not self._stop.is_set():
                if not self._read_one_request(conn):
                    return
                request_index += 1
                if request_index <= self.healthy_requests:
                    headers = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: " + str(len(self._CHAT_BODY)).encode("ascii") +
                        b"\r\nConnection: keep-alive\r\n\r\n")
                    conn.sendall(headers + self._CHAT_BODY)
                    continue
                # The fault: valid headers (plus, for SSE, one complete valid event so the
                # stream parser genuinely starts consuming it), then silence forever.
                if self.stall_body == "sse":
                    headers = (
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Connection: close\r\n\r\n")
                    conn.sendall(headers + self._SSE_FIRST_CHUNK)
                else:
                    headers = (
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Content-Length: 100000\r\nConnection: close\r\n\r\n")
                    conn.sendall(headers + b'{"id":"stall_that_never_finishes"')
                self._stop.wait()           # hold the connection open until teardown
                return
        except OSError:
            return

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass


def _pinned_client(server: _FaultInjectionServer, *, read_timeout: float, monkeypatch,
                   max_retries: int = 0):
    """Build a real together SDK client via the exact function production code uses,
    pointed at the local fault-injection server."""
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-test-fault-injection-not-a-real-key")
    monkeypatch.setenv("TOGETHER_BASE_URL", f"http://127.0.0.1:{server.port}")
    return ac.build_pinned_together_client(
        http_timeout={"connect": 2.0, "read": read_timeout, "write": 2.0, "pool": 2.0},
        sdk_internal_max_retries=max_retries)


def _run_bounded(fn, *, safety_bound: float, gap_message: str):
    """Run ``fn`` on a background thread; join with ``safety_bound``.

    If ``fn`` has not returned within the bound, fail loudly (never hang the suite) with
    ``gap_message`` -- used whenever the assertion IS the fault-injection question itself:
    "did the configured guarantee fire in time".
    """
    result: dict = {}

    def _target():
        started = time.monotonic()
        try:
            result["value"] = fn()
        except BaseException as exc:            # noqa: BLE001 - re-raised on the main thread
            result["exc"] = exc
        result["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=safety_bound)
    assert not thread.is_alive(), gap_message
    return result


def test_read_timeout_fires_on_a_full_body_stall_non_streaming(monkeypatch):
    """The core fault injection: headers arrive, the body never finishes, non-streaming path
    (``RejudgeClient.complete``'s ``self._client().chat.completions.create(...)`` branch)."""
    server = _FaultInjectionServer()
    try:
        read_timeout = 0.5
        client = _pinned_client(server, read_timeout=read_timeout, monkeypatch=monkeypatch)

        def _attempt():
            client.chat.completions.create(
                model="test-model", messages=MSGS, max_tokens=8, temperature=0, seed=0)

        result = _run_bounded(
            _attempt, safety_bound=read_timeout * 20 + 5,
            gap_message=(
                "GAP: the configured read timeout did NOT fire on a zero-bytes-forever "
                "mid-body stall (non-streaming path) within a generous bounded wait"))
        assert isinstance(result.get("exc"), (httpx.TimeoutException, together.APITimeoutError)), (
            f"expected a timeout exception, got {result.get('exc')!r} / {result.get('value')!r}")
        assert result["elapsed"] < read_timeout * 10, (
            f"fired, but only after {result['elapsed']:.2f}s against a {read_timeout}s pin")
    finally:
        server.close()


def test_read_timeout_fires_on_a_full_body_stall_streaming(monkeypatch):
    """Same fault, but through ``RejudgeClient.complete``'s streaming branch
    (``_streamed_create``), the code path 3 of the 4 continuing phase-2 judges actually use
    (``streaming_pinned_models`` in role_limits v6)."""
    server = _FaultInjectionServer(stall_body="sse")
    try:
        read_timeout = 0.5
        client = _pinned_client(server, read_timeout=read_timeout, monkeypatch=monkeypatch)
        rc = ac.RejudgeClient(
            approved_cap_usd=10.0, _sdk_client=client, max_retries=0, _sleep=lambda s: None,
            streaming_pinned_models=frozenset({"test-model"}))

        def _attempt():
            return rc.complete(MSGS, "test-model", 0.0, 1, 8)

        result = _run_bounded(
            _attempt, safety_bound=read_timeout * 20 + 5,
            gap_message=(
                "GAP: the configured read timeout did NOT fire on a zero-bytes-forever "
                "mid-body stall (streaming/SSE path) within a generous bounded wait"))
        assert isinstance(result.get("exc"), RuntimeError), (
            f"expected the exhausted-retries RuntimeError, got {result.get('exc')!r}")
        assert "API call failed" in str(result["exc"])
        # Conservatively charged, exactly like any other transport failure -- never free, never
        # trusted as a success.
        assert rc.actual_spent_usd == 0
        assert rc.uncertain_spend_usd > 0
        assert rc.usage_events[-1]["status"] == "unknown_charge"
    finally:
        server.close()


def test_read_timeout_fires_on_a_reused_pooled_connection(monkeypatch):
    """The first request over a persistent client succeeds in full (proving the connection is
    genuinely kept alive and reused, not reconnected); the SECOND request on that same
    connection then stalls mid-body. Proves connection reuse does not bypass the read pin."""
    server = _FaultInjectionServer(healthy_requests=1)
    try:
        read_timeout = 0.5
        client = _pinned_client(server, read_timeout=read_timeout, monkeypatch=monkeypatch)

        first = client.chat.completions.create(
            model="test-model", messages=MSGS, max_tokens=8, temperature=0, seed=0)
        assert first.choices[0].message.content == "OK"

        def _second_attempt():
            client.chat.completions.create(
                model="test-model", messages=MSGS, max_tokens=8, temperature=0, seed=1)

        result = _run_bounded(
            _second_attempt, safety_bound=read_timeout * 20 + 5,
            gap_message=(
                "GAP: the configured read timeout did NOT fire on a mid-body stall over a "
                "REUSED pooled connection within a generous bounded wait"))
        assert isinstance(result.get("exc"), (httpx.TimeoutException, together.APITimeoutError))
    finally:
        server.close()


def test_create_accounted_client_never_threaded_the_v5_pins_the_2026_08_10_wiring_gap(tmp_path):
    """The concrete, verified root cause: ``rejudge/runner.py`` (the main-run driver) builds
    its live client through ``run_accounting.create_accounted_client``, and -- before the
    additive extension in this change -- that function accepted no ``http_timeout``,
    ``sdk_internal_max_retries``, or ``per_call_wall_clock_ceiling_seconds`` parameters at
    all. So on 2026-08-10 that driver's live client had ``http_timeout=None`` regardless of
    the role-limits v6 pins being correctly hash-bound in their own artifact, and fell back to
    the Together SDK's own unpinned default (``httpx.Timeout(timeout=60, connect=5.0)``) --
    NOT the pinned 120s read / 1200s wall-clock ceiling. This is a regression test for the
    LEGACY call shape (no pins passed): it must keep producing an unpinned client so the
    finding stays falsifiable, and a NEW caller must pass the pins explicitly to get them
    (see the sibling coverage in tests/test_run_accounting.py)."""
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    client, _summary = run_accounting.create_accounted_client(
        approved_cap_usd=1.0, dry_run=False,
        model_prices={"m": {"in": 1.0, "out": 1.0}},
        usage_log_path=ledger, error_log_path=tmp_path / "errors.jsonl",
        ledger_identity=identity)
    assert client.http_timeout is None
    assert client.per_call_wall_clock_ceiling_seconds is None
    # With no http_timeout pinned, the new proactive deadline watchdog (Section 2 below) has
    # nothing to derive a deadline from either -- so, unchanged from before this change, the
    # main-run driver's client had NEITHER guarantee active for any call.
    assert client._transport_deadline_seconds() is None


# --- Section 2: the proactive per-call deadline watchdog -------------------------------------


class _Usage:
    prompt_tokens = 5
    completion_tokens = 2


class _Choice:
    class message:
        content = "YES"


class _Resp:
    usage = _Usage()
    choices = [_Choice()]


class HangingSDK:
    """A create() that blocks forever (never returns, never raises) -- standing in for ANY
    reason a real transport call might never come back, not only the httpx read-pin gap this
    file's Section 1 already shows is otherwise sound. Proves the watchdog is what rescues
    ``complete()``, independent of real sockets."""

    def __init__(self) -> None:
        self.calls = 0

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                threading.Event().wait()          # blocks forever; never returns

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class HangingStreamSDK:
    """Like HangingSDK, but the hang is inside stream iteration (``_streamed_create``'s
    ``for chunk in stream``), not inside ``create()`` itself."""

    def __init__(self) -> None:
        self.calls = 0

        outer = self

        class _HangingIterator:
            def __iter__(self):
                return self

            def __next__(self):
                threading.Event().wait()          # blocks forever mid-stream

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                return _HangingIterator()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class SlowSDK:
    """create() that finishes after a short, bounded delay -- for proving the watchdog leaves
    a call that finishes comfortably inside its deadline completely alone."""

    def __init__(self, delay: float) -> None:
        self.calls = 0
        self.delay = delay

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                time.sleep(outer.delay)
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


_V5_HTTP_TIMEOUT = {"connect": 0.05, "read": 0.05, "write": 2.0, "pool": 2.0}


def test_transport_deadline_is_none_when_http_timeout_unset():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    assert c._transport_deadline_seconds() is None


def test_transport_deadline_is_connect_plus_read_plus_margin():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True, http_timeout=_V5_HTTP_TIMEOUT)
    assert c._transport_deadline_seconds() == pytest.approx(
        0.05 + 0.05 + ac.RejudgeClient.TRANSPORT_DEADLINE_MARGIN_SECONDS)


def test_transport_deadline_never_tighter_than_the_wall_clock_ceiling():
    """A legitimately slow multi-chunk stream that the (larger, deliberately chosen) passive
    wall-clock ceiling would have accepted must not be aborted early by the proactive
    deadline -- the watchdog is defense-in-depth ON TOP OF that ceiling, never stricter."""
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, dry_run=True, http_timeout=_V5_HTTP_TIMEOUT,
        per_call_wall_clock_ceiling_seconds=1200)
    assert c._transport_deadline_seconds() == 1200.0


def test_deadline_watchdog_abandons_a_call_that_never_returns(monkeypatch):
    # The production margin (30s) is deliberately generous to avoid false positives; shrink it
    # here so the test waits milliseconds, not tens of seconds, while exercising the exact same
    # _transport_deadline_seconds/_call_with_deadline code path.
    monkeypatch.setattr(ac.RejudgeClient, "TRANSPORT_DEADLINE_MARGIN_SECONDS", 0.01)
    sdk = HangingSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=0, _sleep=lambda s: None,
        http_timeout={"connect": 0.02, "read": 0.02, "write": 2.0, "pool": 2.0})
    deadline = c._transport_deadline_seconds()

    result = _run_bounded(
        lambda: c.complete(MSGS, "m", 0.1, 1, 64), safety_bound=deadline * 20 + 5,
        gap_message="the deadline watchdog did not abandon a call that never returns")
    assert isinstance(result.get("exc"), RuntimeError)
    assert "API call failed" in str(result["exc"])
    assert result["elapsed"] < deadline * 10
    # Conservative charging: exactly like a genuine read-timeout abandon, never free.
    assert c.actual_spent_usd == 0
    assert c.uncertain_spend_usd > 0
    event = c.usage_events[-1]
    assert event["status"] == "unknown_charge"
    assert "transport deadline" in event["error"]


def test_deadline_watchdog_applies_to_the_streaming_path_too(monkeypatch):
    monkeypatch.setattr(ac.RejudgeClient, "TRANSPORT_DEADLINE_MARGIN_SECONDS", 0.01)
    sdk = HangingStreamSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=0, _sleep=lambda s: None,
        http_timeout={"connect": 0.02, "read": 0.02, "write": 2.0, "pool": 2.0},
        streaming_pinned_models=frozenset({"m"}))
    deadline = c._transport_deadline_seconds()

    result = _run_bounded(
        lambda: c.complete(MSGS, "m", 0.1, 1, 64), safety_bound=deadline * 20 + 5,
        gap_message="the deadline watchdog did not abandon a hung streaming attempt")
    assert isinstance(result.get("exc"), RuntimeError)
    assert c.usage_events[-1]["status"] == "unknown_charge"


def test_deadline_watchdog_does_not_interfere_with_a_call_that_finishes_in_time():
    sdk = SlowSDK(delay=0.01)
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk,
        http_timeout={"connect": 1.0, "read": 1.0, "write": 2.0, "pool": 2.0})
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert c.usage_events[-1]["status"] == "success"


def test_deadline_watchdog_inactive_without_an_http_timeout_pin():
    """Legacy behavior, unchanged: with no http_timeout pinned (exactly the 2026-08-10
    main-run client's actual configuration), the new watchdog never activates -- a slow
    attempt is not newly aborted just because this change exists."""
    sdk = SlowSDK(delay=0.02)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk)
    assert c._transport_deadline_seconds() is None
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert c.usage_events[-1]["status"] == "success"
