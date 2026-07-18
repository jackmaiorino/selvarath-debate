"""Together client wrapper: retries/backoff, cost cap, context guard, dry-run tagging.

The real SDK is imported lazily and only when needed, so tests never touch it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path
from types import SimpleNamespace


class CapExceededError(RuntimeError):
    pass


class ContextGuardError(RuntimeError):
    pass


class UnknownModelPriceError(ValueError):
    pass


class UsageLedgerError(RuntimeError, ValueError):
    pass


class AccountingInvariantError(RuntimeError):
    pass


USAGE_LEDGER_SCHEMA_VERSION = 1
_LIVE_ACCOUNTING_FACTORY_TOKEN = object()


@dataclass(frozen=True)
class UsageLedgerSnapshot:
    path: Path
    state_path: Path
    identity: dict[str, object]
    summary: dict[str, float | int]
    last_sequence: int
    last_event_hash: str


_DRY = {
    "query": "CLAIM: [DRY RUN] the sky over the capital is described as blue",
    "oracle": "YES [DRY RUN]",
    "verdict": "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: [DRY RUN] synthetic response.",
}


def _estimate_usage(messages, max_tokens) -> tuple[int, int]:
    """Conservative prompt/completion token upper bound used for reservations.

    Provider-reported usage always replaces this estimate after a successful call.
    UTF-8 bytes upper-bound byte-fallback tokens; the fixed allowance covers chat
    framing and special tokens.  This intentionally over-reserves relative to the
    usual four-characters-per-token heuristic because the approved cap is a safety
    boundary, not a cost forecast.
    """
    prompt_bound = 64
    for message in messages:
        prompt_bound += 32
        prompt_bound += len(str(message.get("role", "")).encode("utf-8"))
        prompt_bound += len(str(message.get("content", "")).encode("utf-8"))
    return prompt_bound, max_tokens


def _estimate_tokens(messages, max_tokens):
    prompt, completion = _estimate_usage(messages, max_tokens)
    return prompt + completion


def _empty_usage_summary() -> dict[str, float | int]:
    return {"events": 0, "actual_spend_usd": 0.0,
            "uncertain_spend_usd": 0.0, "accounted_spend_usd": 0.0,
            "unmatched_reservations": 0}


def usage_ledger_state_path(path: str | os.PathLike[str]) -> Path:
    ledger = Path(path)
    return ledger.with_name(f"{ledger.name}.state.json")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _usage_event_hash(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_usage_events(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise UsageLedgerError(f"could not read usage ledger {path}: {exc}") from exc
    events: list[dict] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UsageLedgerError(
                f"invalid usage ledger event at {path}:{line_number}") from exc
        if not isinstance(event, dict):
            raise UsageLedgerError(
                f"usage ledger event is not an object at {path}:{line_number}")
        events.append(event)
    return events


def _usage_cost(event: dict, path: Path, event_number: int) -> float:
    try:
        cost = float(event["cost_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageLedgerError(
            f"invalid usage cost at {path}:event {event_number}") from exc
    if not math.isfinite(cost) or cost < 0:
        raise UsageLedgerError(
            f"invalid usage cost at {path}:event {event_number}: {cost!r}")
    return cost


def _summarize_usage_events(
    events: list[dict], path: Path, *, strict_lifecycle: bool,
) -> dict[str, float | int]:
    actual = uncertain = 0.0
    reservations: dict[str, tuple[float, dict]] = {}
    terminal_attempts: set[str] = set()
    terminal_statuses = {"success", "charged_malformed", "unknown_charge",
                         "released_no_charge"}
    stable_fields = ("model", "kind", "seed", "attempt", "estimated_tokens", "metadata")

    for event_number, event in enumerate(events, 1):
        status = event.get("status")
        cost = _usage_cost(event, path, event_number)
        attempt_id = event.get("attempt_id")
        if status == "reserved":
            if not isinstance(attempt_id, str) or not attempt_id:
                raise UsageLedgerError(
                    f"reservation missing attempt_id at {path}:event {event_number}")
            if attempt_id in reservations or attempt_id in terminal_attempts:
                raise UsageLedgerError(
                    f"duplicate usage attempt at {path}:event {event_number}")
            reservations[attempt_id] = (cost, event)
            continue

        if status not in terminal_statuses:
            raise UsageLedgerError(
                f"unknown usage status at {path}:event {event_number}: {status!r}")
        if attempt_id is None:
            if strict_lifecycle:
                raise UsageLedgerError(
                    f"terminal event missing attempt_id at {path}:event {event_number}")
            # Compatibility for audited, pre-chain one-event ledgers. Chained live ledgers
            # never admit a terminal event without its durable pre-call reservation.
            if status in {"success", "charged_malformed"}:
                actual += cost
            elif status == "unknown_charge":
                uncertain += cost
            elif cost != 0:
                raise UsageLedgerError(
                    f"released event has nonzero cost at {path}:event {event_number}")
            continue
        if not isinstance(attempt_id, str) or attempt_id not in reservations:
            raise UsageLedgerError(
                f"terminal event without reservation at {path}:event {event_number}")
        if attempt_id in terminal_attempts:
            raise UsageLedgerError(
                f"duplicate terminal usage event at {path}:event {event_number}")

        reserved_cost, reservation = reservations.pop(attempt_id)
        terminal_attempts.add(attempt_id)
        if strict_lifecycle:
            changed = [field for field in stable_fields
                       if reservation.get(field) != event.get(field)]
            if changed:
                raise UsageLedgerError(
                    f"terminal event changed reservation identity at {path}:event "
                    f"{event_number}: {', '.join(changed)}")
        tolerance = max(1e-12, reserved_cost * 1e-12)
        if status in {"success", "charged_malformed"}:
            if cost > reserved_cost + tolerance:
                raise UsageLedgerError(
                    f"terminal cost exceeds reservation at {path}:event {event_number}: "
                    f"${cost:.8f} > ${reserved_cost:.8f}")
            actual += cost
        elif status == "unknown_charge":
            if not math.isclose(cost, reserved_cost, rel_tol=0.0, abs_tol=tolerance):
                raise UsageLedgerError(
                    f"unknown charge does not equal reservation at {path}:event "
                    f"{event_number}")
            uncertain += cost
        elif cost != 0:
            raise UsageLedgerError(
                f"released event has nonzero cost at {path}:event {event_number}")

    uncertain += sum(cost for cost, _event in reservations.values())
    return {"events": len(events), "actual_spend_usd": actual,
            "uncertain_spend_usd": uncertain,
            "accounted_spend_usd": actual + uncertain,
            "unmatched_reservations": len(reservations)}


def _ledger_identity(path: Path, ledger_id: str) -> dict[str, object]:
    return {
        "schema_version": USAGE_LEDGER_SCHEMA_VERSION,
        "ledger_id": ledger_id,
        "ledger_path": path.resolve().as_posix(),
        "state_path": usage_ledger_state_path(path).resolve().as_posix(),
    }


def _validate_usage_chain(events: list[dict], path: Path) -> tuple[dict[str, object], list[str]]:
    if not events or events[0].get("status") != "ledger_genesis":
        raise UsageLedgerError(f"usage ledger has no chained genesis event: {path}")
    genesis = events[0]
    ledger_id = genesis.get("ledger_id")
    if (not isinstance(ledger_id, str) or not ledger_id
            or genesis.get("schema_version") != USAGE_LEDGER_SCHEMA_VERSION
            or genesis.get("sequence") != 0 or genesis.get("prev_event_hash") is not None):
        raise UsageLedgerError(f"invalid usage ledger genesis event: {path}")

    hashes: list[str] = []
    previous: str | None = None
    for sequence, event in enumerate(events):
        if (event.get("ledger_id") != ledger_id or event.get("sequence") != sequence
                or isinstance(event.get("sequence"), bool)
                or event.get("prev_event_hash") != previous):
            raise UsageLedgerError(
                f"usage ledger chain discontinuity at {path}:sequence {sequence}")
        event_hash = event.get("event_hash")
        if (not isinstance(event_hash, str) or len(event_hash) != 64
                or event_hash != _usage_event_hash(event)):
            raise UsageLedgerError(
                f"usage ledger hash mismatch at {path}:sequence {sequence}")
        hashes.append(event_hash)
        previous = event_hash
    return _ledger_identity(path, ledger_id), hashes


def _read_usage_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UsageLedgerError(f"could not read usage ledger state {path}: {exc}") from exc
    expected = {"schema_version", "ledger_id", "last_sequence", "last_event_hash"}
    if not isinstance(state, dict) or set(state) != expected:
        raise UsageLedgerError(f"invalid usage ledger state: {path}")
    if (state["schema_version"] != USAGE_LEDGER_SCHEMA_VERSION
            or not isinstance(state["ledger_id"], str)
            or not isinstance(state["last_sequence"], int)
            or isinstance(state["last_sequence"], bool)
            or state["last_sequence"] < 0
            or not isinstance(state["last_event_hash"], str)):
        raise UsageLedgerError(f"invalid usage ledger state fields: {path}")
    return state


def _usage_state_payload(identity: dict[str, object], sequence: int,
                         event_hash: str) -> dict:
    return {
        "schema_version": USAGE_LEDGER_SCHEMA_VERSION,
        "ledger_id": identity["ledger_id"],
        "last_sequence": sequence,
        "last_event_hash": event_hash,
    }


def load_chained_usage_ledger(
    path: str | os.PathLike[str], *, expected_identity: dict[str, object] | None = None,
) -> UsageLedgerSnapshot:
    ledger = Path(path)
    if not ledger.exists():
        raise UsageLedgerError(f"required usage ledger is missing: {ledger}")
    events = _read_usage_events(ledger)
    identity, hashes = _validate_usage_chain(events, ledger)
    if expected_identity is not None and identity != expected_identity:
        raise UsageLedgerError(
            f"usage ledger identity does not match the run manifest: {ledger}")

    state_path = usage_ledger_state_path(ledger)
    if not state_path.exists():
        if len(events) != 1:
            raise UsageLedgerError(
                f"usage ledger state is missing after paid events: {state_path}")
        # Safe recovery from a crash between fsyncing a brand-new genesis and publishing
        # its state. No provider request can begin before both files exist.
        _atomic_write_json(state_path, _usage_state_payload(identity, 0, hashes[0]))
    state = _read_usage_state(state_path)
    if state["ledger_id"] != identity["ledger_id"]:
        raise UsageLedgerError(f"usage ledger/state identity mismatch: {ledger}")
    state_sequence = state["last_sequence"]
    if state_sequence >= len(hashes):
        raise UsageLedgerError(
            f"usage ledger was truncated behind its durable state: {ledger}")
    if hashes[state_sequence] != state["last_event_hash"]:
        raise UsageLedgerError(
            f"usage ledger diverges from its durable state: {ledger}")
    tail_sequence = len(hashes) - 1
    if state_sequence < tail_sequence:
        # The event is fsynced before state publication. A ledger-ahead state is therefore
        # the expected, recoverable ordering after a crash; roll the state forward only.
        _atomic_write_json(
            state_path, _usage_state_payload(identity, tail_sequence, hashes[-1]))

    usage_events = events[1:]
    summary = _summarize_usage_events(usage_events, ledger, strict_lifecycle=True)
    return UsageLedgerSnapshot(
        path=ledger, state_path=state_path, identity=identity, summary=summary,
        last_sequence=tail_sequence, last_event_hash=hashes[-1])


def prepare_usage_ledger(
    path: str | os.PathLike[str], *, allow_create: bool,
) -> dict[str, object]:
    """Create a no-spend chained ledger or validate an existing ledger and tail state.

    ``allow_create`` must only be true before an output manifest exists. Runners bind the
    returned random genesis identity into that immutable manifest. Consequently deleting
    and recreating both ledger files produces a new identity and the manifest refuses it.
    """
    ledger = Path(path)
    if not ledger.exists():
        if not allow_create:
            raise UsageLedgerError(f"required usage ledger is missing: {ledger}")
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger_id = uuid.uuid4().hex
        genesis = {
            "status": "ledger_genesis",
            "schema_version": USAGE_LEDGER_SCHEMA_VERSION,
            "ledger_id": ledger_id,
            "sequence": 0,
            "prev_event_hash": None,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        genesis["event_hash"] = _usage_event_hash(genesis)
        try:
            with ledger.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(genesis, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
    elif ledger.stat().st_size == 0:
        if not allow_create:
            raise UsageLedgerError(f"required usage ledger is empty: {ledger}")
        ledger.unlink()
        return prepare_usage_ledger(ledger, allow_create=True)

    snapshot = load_chained_usage_ledger(ledger)
    if allow_create and snapshot.last_sequence > 0:
        raise UsageLedgerError(
            f"paid usage ledger exists without a bound run manifest: {ledger}")
    return snapshot.identity


def summarize_usage_log(path) -> dict[str, float | int]:
    """Read a usage ledger and return conservative cumulative spend.

    New live ledgers are hash-chained and checked against a durable tail state. Legacy
    one-event ledgers remain readable for audit, but the live accounting factory refuses
    to use them for a new or resumed provider run.
    """
    path = Path(path)
    if not path.exists():
        return _empty_usage_summary()
    events = _read_usage_events(path)
    if events and events[0].get("status") == "ledger_genesis":
        return load_chained_usage_ledger(path).summary
    return _summarize_usage_events(events, path, strict_lifecycle=False)


class RejudgeClient:
    def __init__(self, approved_cap_usd, price_per_mtok=1.04, dry_run=False,
                 error_log_path=None, max_context_tokens=131072, max_retries=4,
                 _sdk_client=None, _sleep=time.sleep, *, model_prices=None,
                 strict_model_pricing=False, initial_spend_usd=0.0,
                 initial_uncertain_spend_usd=0.0, usage_log_path=None,
                 _ledger_snapshot: UsageLedgerSnapshot | None = None,
                 _accounting_factory_token=None):
        self.approved_cap_usd = float(approved_cap_usd)
        self.price_per_mtok = price_per_mtok
        self.model_prices = dict(model_prices or {})
        self.strict_model_pricing = strict_model_pricing
        self.initial_spend_usd = float(initial_spend_usd)
        initial_uncertain_spend_usd = float(initial_uncertain_spend_usd)
        for name, value in (("approved_cap_usd", self.approved_cap_usd),
                            ("initial_spend_usd", self.initial_spend_usd),
                            ("initial_uncertain_spend_usd", initial_uncertain_spend_usd)):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        if self.initial_spend_usd + initial_uncertain_spend_usd > self.approved_cap_usd:
            raise ValueError(
                "prior accounted spend exceeds the approved cumulative cap")
        self.dry_run = dry_run
        self.error_log_path = error_log_path
        self.usage_log_path = usage_log_path
        if (not self.dry_run and _sdk_client is None
                and _accounting_factory_token is not _LIVE_ACCOUNTING_FACTORY_TOKEN):
            raise ValueError(
                "live provider clients must be created by create_accounted_client")
        if _ledger_snapshot is not None:
            if self.dry_run or not self.usage_log_path:
                raise ValueError("a durable ledger snapshot is valid only for live clients")
            if Path(self.usage_log_path).resolve() != _ledger_snapshot.path.resolve():
                raise ValueError("usage_log_path does not match the prepared ledger snapshot")
        if (not self.dry_run and _sdk_client is None
                and (not self.strict_model_pricing or _ledger_snapshot is None)):
            raise ValueError(
                "live provider clients require strict prices and a prepared durable ledger")
        if self.error_log_path:
            error_path = Path(self.error_log_path)
            error_path.parent.mkdir(parents=True, exist_ok=True)
            with error_path.open("a", encoding="utf-8"):
                pass
        if self.usage_log_path:
            usage_path = Path(self.usage_log_path)
            usage_path.parent.mkdir(parents=True, exist_ok=True)
            # Refuse before any paid request if the durable ledger is not writable.
            with usage_path.open("a", encoding="utf-8"):
                pass
        self.max_context_tokens = max_context_tokens
        self.max_retries = max_retries
        self._sdk = _sdk_client
        self._sleep = _sleep
        self._lock = threading.Lock()
        self.total_tokens = 0
        self.actual_prompt_tokens = 0
        self.actual_completion_tokens = 0
        self.uncertain_tokens = 0
        self._actual_spend_usd = 0.0
        self._uncertain_spend_usd = initial_uncertain_spend_usd
        self._active_reservations_usd = 0.0
        self._usage_events = []
        self._fatal_accounting_error: str | None = None
        self._ledger_identity = (_ledger_snapshot.identity if _ledger_snapshot else None)
        self._ledger_sequence = (_ledger_snapshot.last_sequence if _ledger_snapshot else None)
        self._ledger_event_hash = (_ledger_snapshot.last_event_hash
                                   if _ledger_snapshot else None)
        self._ledger_state_path = (_ledger_snapshot.state_path if _ledger_snapshot else None)
        self._streaming_models = set()   # endpoints that reject non-streaming calls

    @property
    def spent_usd(self) -> float:
        """Conservative cumulative spend used for the hard cap.

        Includes prior reconciled spend, provider-reported usage, active reservations,
        and attempts whose billing status is unknown (for example, a timeout). Unknown
        attempts remain reserved until reconciled against provider billing rather than
        being silently treated as free.
        """
        with self._lock:
            return (self.initial_spend_usd + self._actual_spend_usd
                    + self._uncertain_spend_usd + self._active_reservations_usd)

    @property
    def actual_spent_usd(self) -> float:
        with self._lock:
            return self.initial_spend_usd + self._actual_spend_usd

    @property
    def uncertain_spend_usd(self) -> float:
        with self._lock:
            return self._uncertain_spend_usd

    @property
    def usage_events(self) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self._usage_events]

    def _prices_for(self, model: str) -> tuple[float, float]:
        entry = self.model_prices.get(model)
        if entry is None:
            if self.strict_model_pricing:
                raise UnknownModelPriceError(
                    f"no frozen input/output prices configured for model {model!r}")
            fallback = float(self.price_per_mtok)
            if not math.isfinite(fallback) or fallback < 0:
                raise UnknownModelPriceError(
                    f"fallback model price must be finite and non-negative: {fallback!r}")
            return fallback, fallback
        try:
            if isinstance(entry, dict):
                prices = float(entry["in"]), float(entry["out"])
            else:
                input_price, output_price = entry
                prices = float(input_price), float(output_price)
        except (KeyError, TypeError, ValueError) as exc:
            raise UnknownModelPriceError(
                f"invalid price entry for model {model!r}: {entry!r}") from exc
        if any(not math.isfinite(price) or price < 0 for price in prices):
            raise UnknownModelPriceError(
                f"model prices must be finite and non-negative for {model!r}: {entry!r}")
        return prices

    @staticmethod
    def _cost(prompt_tokens: int, completion_tokens: int,
              input_price: float, output_price: float) -> float:
        return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000

    def _record_usage_event(self, event: dict) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        if self.usage_log_path:
            if self._ledger_identity is not None:
                assert self._ledger_sequence is not None
                assert self._ledger_event_hash is not None
                event = {
                    **event,
                    "ledger_id": self._ledger_identity["ledger_id"],
                    "sequence": self._ledger_sequence + 1,
                    "prev_event_hash": self._ledger_event_hash,
                }
                event["event_hash"] = _usage_event_hash(event)
            with open(self.usage_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            if self._ledger_identity is not None:
                assert self._ledger_state_path is not None
                _atomic_write_json(
                    self._ledger_state_path,
                    _usage_state_payload(
                        self._ledger_identity, event["sequence"], event["event_hash"]),
                )
                self._ledger_sequence = event["sequence"]
                self._ledger_event_hash = event["event_hash"]
        self._usage_events.append(event)

    def _latch_accounting_error(self, exc: Exception) -> UsageLedgerError:
        self._fatal_accounting_error = str(exc)
        return UsageLedgerError(f"usage accounting is no longer safe: {exc}")

    def _reserve_attempt(self, *, model: str, prompt_tokens: int,
                         completion_tokens: int, kind: str, seed: int, attempt: int,
                         request_metadata: dict | None) -> tuple[float, float, float, str]:
        input_price, output_price = self._prices_for(model)
        estimated_cost = self._cost(
            prompt_tokens, completion_tokens, input_price, output_price)
        attempt_id = uuid.uuid4().hex
        with self._lock:
            if self._fatal_accounting_error is not None:
                raise UsageLedgerError(
                    f"usage accounting is latched unsafe: {self._fatal_accounting_error}")
            projected = (self.initial_spend_usd + self._actual_spend_usd
                         + self._uncertain_spend_usd + self._active_reservations_usd
                         + estimated_cost)
            if projected > self.approved_cap_usd:
                raise CapExceededError(
                    f"projected spend ${projected:.4f} > approved cap "
                    f"${self.approved_cap_usd:.4f}")
            self._active_reservations_usd += estimated_cost
            self.total_tokens += prompt_tokens + completion_tokens
            try:
                self._record_usage_event({
                    "status": "reserved", "attempt_id": attempt_id,
                    "model": model, "kind": kind, "seed": seed, "attempt": attempt,
                    "prompt_tokens": None, "completion_tokens": None,
                    "estimated_tokens": prompt_tokens + completion_tokens,
                    "cost_usd": estimated_cost,
                    "metadata": request_metadata or {},
                })
            except Exception as exc:
                self._active_reservations_usd -= estimated_cost
                self.total_tokens -= prompt_tokens + completion_tokens
                raise self._latch_accounting_error(exc) from exc
        return input_price, output_price, estimated_cost, attempt_id

    def _mark_unknown(self, *, estimated_cost: float, estimated_tokens: int,
                      model: str, kind: str, seed: int, attempt: int,
                      attempt_id: str, exc: Exception,
                      request_metadata: dict | None) -> None:
        with self._lock:
            try:
                self._record_usage_event({
                    "status": "unknown_charge", "attempt_id": attempt_id,
                    "model": model, "kind": kind, "seed": seed, "attempt": attempt,
                    "prompt_tokens": None, "completion_tokens": None,
                    "estimated_tokens": estimated_tokens,
                    "cost_usd": estimated_cost, "error": str(exc),
                    "metadata": request_metadata or {},
                })
            except Exception as ledger_exc:
                raise self._latch_accounting_error(ledger_exc) from ledger_exc
            self._active_reservations_usd -= estimated_cost
            self._uncertain_spend_usd += estimated_cost
            self.uncertain_tokens += estimated_tokens

    def _release_reservation(self, estimated_cost: float, estimated_tokens: int, *,
                             attempt_id: str, model: str, kind: str, seed: int,
                             attempt: int, request_metadata: dict | None) -> None:
        with self._lock:
            try:
                self._record_usage_event({
                    "status": "released_no_charge", "attempt_id": attempt_id,
                    "model": model, "kind": kind, "seed": seed, "attempt": attempt,
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "estimated_tokens": estimated_tokens, "cost_usd": 0.0,
                    "metadata": request_metadata or {},
                })
            except Exception as exc:
                raise self._latch_accounting_error(exc) from exc
            self._active_reservations_usd -= estimated_cost
            self.total_tokens -= estimated_tokens

    def _reconcile_success(self, *, estimated_cost: float, estimated_tokens: int,
                           prompt_tokens: int, completion_tokens: int,
                           input_price: float, output_price: float, model: str,
                           kind: str, seed: int, attempt: int, status: str,
                           attempt_id: str, request_metadata: dict | None) -> None:
        actual_cost = self._cost(
            prompt_tokens, completion_tokens, input_price, output_price)
        with self._lock:
            try:
                self._record_usage_event({
                    "status": status, "attempt_id": attempt_id,
                    "model": model, "kind": kind, "seed": seed, "attempt": attempt,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "estimated_tokens": estimated_tokens,
                    "cost_usd": actual_cost,
                    "metadata": request_metadata or {},
                })
            except Exception as exc:
                raise self._latch_accounting_error(exc) from exc
            self._active_reservations_usd -= estimated_cost
            self._actual_spend_usd += actual_cost
            self.total_tokens += prompt_tokens + completion_tokens - estimated_tokens
            self.actual_prompt_tokens += prompt_tokens
            self.actual_completion_tokens += completion_tokens
            if actual_cost > estimated_cost + 1e-12:
                error = AccountingInvariantError(
                    f"provider cost ${actual_cost:.8f} exceeded conservative reservation "
                    f"${estimated_cost:.8f} for model {model!r}; reconcile billing before resume")
                self._fatal_accounting_error = str(error)
                raise error

    def _streamed_create(self, model, messages, temperature, max_tokens, seed):
        """Call a streaming-only endpoint and reassemble a response-shaped object.

        Accumulates delta content across chunks; usage is taken from the final chunk
        (Together sends it there when stream_options requests it). Returns an object
        with .usage and .choices[0].message.content so the non-streaming accounting
        path applies unchanged.
        """
        stream = self._client().chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, seed=seed, stream=True,
            stream_options={"include_usage": True})
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

        message = SimpleNamespace(content="".join(parts))
        return SimpleNamespace(usage=usage,
                               choices=[SimpleNamespace(message=message)])

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
            api_key = os.environ.get("TOGETHER_API_KEY")
            if api_key is None or not api_key.strip():
                raise ValueError(
                    "TOGETHER_API_KEY environment variable is missing or blank; a live "
                    "Together SDK client cannot be constructed")
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

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata: dict | None = None) -> str:
        if model.startswith(self.REASONING_MODEL_PREFIXES):
            max_tokens = max(max_tokens, self.REASONING_MAX_TOKENS_FLOOR)
        estimated_prompt, estimated_completion = _estimate_usage(messages, max_tokens)
        estimated_tokens = estimated_prompt + estimated_completion
        if estimated_tokens > self.max_context_tokens:
            raise ContextGuardError(
                f"estimated {estimated_tokens} tokens > {self.max_context_tokens}")
        if self.dry_run:
            if kind not in _DRY:
                raise ValueError(f"unknown kind: {kind!r}")
            return _DRY[kind]
        last = None
        for attempt in range(self.max_retries + 1):
            input_price, output_price, estimated_cost, attempt_id = self._reserve_attempt(
                model=model, prompt_tokens=estimated_prompt,
                completion_tokens=estimated_completion, kind=kind, seed=seed,
                attempt=attempt, request_metadata=request_metadata)
            try:
                if model in self._streaming_models:
                    resp = self._streamed_create(model, messages, temperature, max_tokens, seed)
                else:
                    resp = self._client().chat.completions.create(
                        model=model, messages=messages, temperature=temperature,
                        max_tokens=max_tokens, seed=seed)
            except Exception as exc:                     # transient API error, no charge known
                if "streaming_required" in str(exc) or "supports streaming" in str(exc):
                    # Capability negotiation is a rejected request, not an inference. Release
                    # its reservation and retry immediately through the required transport.
                    self._release_reservation(
                        estimated_cost, estimated_tokens, attempt_id=attempt_id,
                        model=model, kind=kind, seed=seed, attempt=attempt,
                        request_metadata=request_metadata)
                    self._streaming_models.add(model)
                    last = exc
                    continue
                last = exc
                self._mark_unknown(
                    estimated_cost=estimated_cost, estimated_tokens=estimated_tokens,
                    model=model, kind=kind, seed=seed, attempt=attempt,
                    attempt_id=attempt_id, exc=exc,
                    request_metadata=request_metadata)
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
                raw_prompt_tokens = resp.usage.prompt_tokens
                raw_completion_tokens = resp.usage.completion_tokens
                if (not isinstance(raw_prompt_tokens, Integral)
                        or isinstance(raw_prompt_tokens, bool)
                        or not isinstance(raw_completion_tokens, Integral)
                        or isinstance(raw_completion_tokens, bool)):
                    raise ValueError("provider usage tokens must be non-negative integers")
                prompt_tokens = int(raw_prompt_tokens)
                completion_tokens = int(raw_completion_tokens)
                if prompt_tokens < 0 or completion_tokens < 0:
                    raise ValueError("provider usage tokens must be non-negative integers")
            except Exception as exc:          # usage itself unreadable -- unknown charge
                last = exc
                self._mark_unknown(
                    estimated_cost=estimated_cost, estimated_tokens=estimated_tokens,
                    model=model, kind=kind, seed=seed, attempt=attempt,
                    attempt_id=attempt_id, exc=exc,
                    request_metadata=request_metadata)
                self._log_error(attempt, model, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
                continue
            try:
                content = resp.choices[0].message.content
            except Exception as exc:
                self._reconcile_success(
                    estimated_cost=estimated_cost, estimated_tokens=estimated_tokens,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    input_price=input_price, output_price=output_price, model=model,
                    kind=kind, seed=seed, attempt=attempt, status="charged_malformed",
                    attempt_id=attempt_id, request_metadata=request_metadata)
                raise RuntimeError(
                    f"malformed API response after successful charge: {exc}") from exc
            self._reconcile_success(
                estimated_cost=estimated_cost, estimated_tokens=estimated_tokens,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                input_price=input_price, output_price=output_price, model=model,
                kind=kind, seed=seed, attempt=attempt, status="success",
                attempt_id=attempt_id, request_metadata=request_metadata)
            return content if content is not None else ""
        raise RuntimeError(f"API call failed after {self.max_retries + 1} attempts: {last}")
