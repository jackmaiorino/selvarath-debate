"""Signature-gated auto-resume supervisor for the live canary run.

The strict client halts the whole run on any call whose billing outcome is ambiguous
(UnknownChargeHalt), which is correct accounting but turns a provider's bad day into
dozens of manual resumes. This supervisor automates EXACTLY the reconciliation the
operator performed by hand, and nothing more: it relaunches only when the newest halt
matches the known-benign transient signature, and stops for manual review otherwise.

The benign signature (all conditions required):
- the run exited with halted_reason UnknownChargeHalt and named a halted cell;
- the recent usage-ledger window contains at least one abandoned call, and EVERY abandoned
  call in it matches the enumerated transient set (provider 5xx, 429 rate limit, SDK
  timeout, truncated stream, or a connection reset/abort). A window rather than the newest
  event since 2026-08-04: under concurrency the calls in flight alongside the one that
  halted finish and append after it, so the newest event is routinely another worker's
  success;
- the newest error-log entry matches that same set and was recorded after this attempt
  started;
- the halted cell has no row in the results file (nothing was half-recorded).

Bounds, all of which stop the supervisor for manual review when crossed:
- at most MAX_RESUMES automatic resumes in one supervisor invocation. This is a
  runaway-loop backstop, not a safety control: the binding safety constraints are the
  same-call limit and the uncertain-spend ceiling. Both have since been relaxed by
  recorded owner decision (amendments 7 and 9), each stating plainly that it loosens a
  real control rather than a backstop; this line said "both unchanged" until then. Raised 60 -> 400 on
  2026-08-01 because Together's checker endpoint halts the run every ~2 cells, so the
  original bound would have stopped the canary with ~300 cells undone;
- at most SAME_CELL_MAX consecutive halts on the same CALL (a deterministic failure
  masquerading as a transient, as the gpt-oss streaming regression did). Keyed per call
  rather than per cell since 2026-08-03: a judgment cell issues several independently
  failing calls, and pooling them tripped the limit on cells that were still healthy;
- uncertain spend must stay under UNCERTAIN_CEILING_USD.

Every decision is one printed line, so a log monitor can follow along. The policy is
recorded append-only in rejudge/phase2_canary_auto_resume_policy_2026-07-28.json; this
script grants nothing and changes no cap.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

MAX_RESUMES = 400
# Raised 3 -> 8 on 2026-08-03 by owner instruction (amendment 9), together with the switch to
# per-CALL keying. Like the uncertain ceiling, this is a real safety control rather than a
# backstop, so the amendment records it as a deliberate relaxation. The mismatch it fixes:
# the limit was calibrated when a hung call cost 600s, and the transport amendment cut that
# to 120s, so a call needing five attempts now trips a limit built for far slower retries.
SAME_CELL_MAX = 8
# Raised 2.00 -> 4.00 on 2026-08-01 by owner instruction (amendment 7). Unlike the resume
# backstop, this IS one of the constraints that bounds real risk, so the amendment records it
# as a deliberate relaxation rather than as housekeeping.
UNCERTAIN_CEILING_USD = 4.00
# The halt window is bounded by THIS ATTEMPT, not by a count of events. A fixed lookback
# cannot be sized correctly: when one cell halts, the driver still finishes the rest of its
# block, so with eight workers over a sixteen-cell block a hundred or more events can append
# after the halt and scroll its own abandoned call out of view. That stopped a healthy run on
# 2026-08-05. Timestamps make the bound exact and need no guess about block shape. The event
# count below is only a read cap so the whole ledger is not parsed each time.
_LEDGER_SCAN_EVENTS = 4000
# Ledger timestamps and the attempt clock can disagree by a little; the grace keeps that from
# discarding the halt's own evidence, and is far shorter than a resume backoff so it cannot
# reach into the previous attempt.
_CLOCK_GRACE_SECONDS = 30
RESUME_BACKOFF_SECONDS = 60
SAME_CELL_EXTRA_BACKOFF_SECONDS = 240

# The enumerated transient set, grown by recorded amendments 1-5 and 8 as Together produced
# each new failure shape: 5xx, 429, SDK timeout, socket-level read timeout (amendment 8: the
# SAME read timeout firing, surfaced through the socket layer rather than the SDK's own
# wording -- the 2026-08-02 instance waited 126s against the pinned 120s), truncated stream,
# connection reset/abort. Each
# may have billed server-side, which is exactly what the permanently-counted uncertain
# reservation and the uncertain ceiling bound; cell-granular retry cannot duplicate a
# result row. The set stays enumerated so a novel anomaly still stops for manual review.
_BENIGN_TRANSIENT = re.compile(
    r"Error code: 5\d\d|Error code: 429|Request timed out\.|"
    r"The read operation timed out|"
    r"streaming response ended without usage chunk|"
    r"Connection reset by peer|Connection aborted|Server disconnected")
_RATE_LIMIT = re.compile(r"Error code: 429")

# Halt reasons a resume can actually fix.
#
# checker_malformed joined this set on 2026-08-05, together with the change that stops the
# per-call cache memoising an empty response. It was terminal only BECAUSE of that caching:
# every resume replayed the same unreadable response, so the cell could never complete, and
# both canaries lost cells to it at roughly one per 199. With empties no longer cached the
# next attempt re-calls the checker, and at temperature 0 a successful call returns the
# decision the failed one should have. The bound against this becoming retry-until-favourable
# is the unchanged same-call limit: a checker that keeps returning nothing for one specific
# call is a deterministic failure and still reaches a human.
#
# checker_unresolved is deliberately NOT here. That is the checker answering in a way the
# frozen parser reads but cannot act on, which at temperature 0 is exactly what it will answer
# again. Retrying it would be retrying for a different answer to the same question.
_RESUMABLE_HALTS = frozenset({"UnknownChargeHalt", "checker_outage", "checker_malformed"})


def _event_epoch(event: dict) -> float:
    """Ledger timestamp as epoch seconds; events without one never count as this attempt's."""
    raw = str(event.get("ts") or "")
    if not raw:
        return float("-inf")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return float("-inf")


def _tail_json_lines(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-n:] if line.strip()]


def benign_transient_signature(*, outcome: dict, usage_path: Path, error_log_path: Path,
                               results_path: Path, attempt_started_at: float) -> str | None:
    """Return None if the halt matches the benign signature, else the refusal reason."""
    # Amendment 3: checker_outage is the gate's wrapper around ANY checker-call exception,
    # including the client's transient failures; the ledger/error-log checks below tell the
    # benign shapes from real checker anomalies (which halt as checker_malformed or
    # checker_unresolved and never enter this set).
    if outcome.get("halted_reason") not in _RESUMABLE_HALTS:
        return (f"halt reason {outcome.get('halted_reason')!r} is not one of "
                f"{sorted(_RESUMABLE_HALTS)}")
    cell = outcome.get("halted_cell_key")
    if not cell:
        return "halt names no cell"
    # Scanned over a window rather than read off the newest event. That shortcut was sound
    # while the canary ran one cell at a time, because the call that halted the run was
    # necessarily the last thing written. Under concurrency the calls already in flight when
    # one halted go on to finish and append after it, so the newest event is routinely another
    # worker's success and reading it as the halt's own outcome stops a healthy run for manual
    # review. The window still has to CONTAIN a benign abandonment, and any abandonment in it
    # whose shape is unenumerated still stops: this loosens which event is inspected, never
    # whether one is required.
    events = _tail_json_lines(usage_path, _LEDGER_SCAN_EVENTS)
    abandoned = [event for event in events
                 if event.get("status") == "unknown_charge"
                 and _event_epoch(event) >= attempt_started_at - _CLOCK_GRACE_SECONDS]
    if not abandoned:
        return ("no abandoned call in this attempt's ledger events; the halt is unexplained")
    unenumerated = [event for event in abandoned
                    if not _BENIGN_TRANSIENT.search(str(event.get("error", "")))]
    if unenumerated:
        return ("an abandoned call in the halt window is not in the enumerated transient set "
                f"(got: {str(unenumerated[-1].get('error', ''))[:120]!r})")
    errors = _tail_json_lines(error_log_path, 1)
    if not errors or not _BENIGN_TRANSIENT.search(str(errors[0].get("error", ""))):
        return ("newest error-log entry is not in the enumerated transient set "
                f"(got: {str(errors[0].get('error', ''))[:120] if errors else ''!r})")
    error_ts = str(errors[0].get("ts", ""))
    if error_ts and time.mktime(time.strptime(
            error_ts[:19], "%Y-%m-%dT%H:%M:%S")) < attempt_started_at - 120:
        return "newest error-log entry predates this attempt"
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("cell_key") == cell:
                return "halted cell already has a result row"
    return None


def repeat_key(cell_key, usage_path: Path) -> str:
    """What "the same failure again" means, for the consecutive-halt limit.

    The limit exists to catch a deterministic failure masquerading as a transient, and
    determinism is a property of a CALL, not of a cell: one judgment cell issues a checker
    call, a judge query per slot, and a verdict, each of which can fail independently.
    Keying the counter on the cell conflated them. On 2026-08-02, with gemma failing about
    45% of calls, that tripped the limit on a cell whose calls were all still succeeding on
    retry -- one had failed three times at 120s and then returned in four seconds.

    So the key is the cell plus the identity of the call that actually halted, read from the
    newest ledger event. A single call failing repeatedly still collapses to one key and
    still stops the run, which is the property worth keeping. Falls back to the bare cell
    when the newest event carries no metadata, restoring the previous behaviour rather than
    becoming silently un-countable.
    """
    events = _tail_json_lines(usage_path, 1)
    metadata = (events[0].get("metadata") or {}) if events else {}
    if not metadata.get("call_role"):
        return str(cell_key)
    return ":".join(str(part) for part in (
        cell_key, metadata.get("call_role"), metadata.get("query_index"),
        metadata.get("attempt")))


def _uncertain_spend(usage_path: Path) -> float:
    total = 0.0
    terminal: dict[str, str] = {}
    for event in _tail_json_lines(usage_path, 10 ** 6):
        status = event.get("status")
        attempt_id = event.get("attempt_id")
        if status == "reserved":
            terminal.setdefault(str(attempt_id), "")
        elif status in ("success", "released_no_charge", "charged_malformed",
                        "unknown_charge"):
            terminal[str(attempt_id)] = str(status)
    for event in _tail_json_lines(usage_path, 10 ** 6):
        if event.get("status") == "unknown_charge":
            total += float(event.get("cost_usd", 0.0))
        elif event.get("status") == "reserved" and not terminal.get(
                str(event.get("attempt_id"))):
            total += float(event.get("cost_usd", 0.0))
    return total


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4:
        print("usage: canary_supervisor.py PYTHON MANIFEST AUTHORIZATION ARCHIVE_DIR "
              "[extra driver args...]", file=sys.stderr)
        return 2
    python, manifest, authorization, archive = args[:4]
    extra = args[4:]
    archive_dir = Path(archive)
    usage_path = archive_dir / "canary_usage.jsonl"
    error_log_path = archive_dir / "canary_error_log.jsonl"
    results_path = archive_dir / "canary_results.jsonl"

    resumes = 0
    last_cell = None
    same_cell_count = 0
    while True:
        started = time.time()
        print(f"supervisor: attempt {resumes + 1} starting", flush=True)
        proc = subprocess.run(
            [python, "-m", "rejudge.phase2_canary_live", "--manifest", manifest,
             "--authorization", authorization, "--project-root", ".",
             "--mode", "subagent-batch", *extra],
            capture_output=True, text=True)
        stdout = proc.stdout.strip().splitlines()
        for line in stdout[-3:]:
            print(f"driver: {line}", flush=True)
        if proc.returncode == 0:
            print("supervisor: converged or worklist exported; done", flush=True)
            return 0
        outcome = {}
        for line in reversed(stdout):
            if line.startswith("{"):
                outcome = json.loads(line)
                break
        if not outcome:
            print(f"supervisor: STOP no outcome line; stderr: {proc.stderr.strip()[-400:]}",
                  flush=True)
            return 3
        reason = benign_transient_signature(
            outcome=outcome, usage_path=usage_path, error_log_path=error_log_path,
            results_path=results_path, attempt_started_at=started)
        if reason is not None:
            print(f"supervisor: STOP for manual review: {reason}", flush=True)
            return 4
        cell = outcome.get("halted_cell_key")
        key = repeat_key(cell, usage_path)
        same_cell_count = same_cell_count + 1 if key == last_cell else 1
        last_cell = key
        if same_cell_count > SAME_CELL_MAX:
            print(f"supervisor: STOP call {key} halted {same_cell_count} consecutive "
                  "times; looks deterministic", flush=True)
            return 5
        resumes += 1
        if resumes > MAX_RESUMES:
            print(f"supervisor: STOP resume budget {MAX_RESUMES} exhausted", flush=True)
            return 6
        uncertain = _uncertain_spend(usage_path)
        if uncertain > UNCERTAIN_CEILING_USD:
            print(f"supervisor: STOP uncertain spend ${uncertain:.3f} exceeds ceiling",
                  flush=True)
            return 7
        backoff = RESUME_BACKOFF_SECONDS + (
            SAME_CELL_EXTRA_BACKOFF_SECONDS if same_cell_count > 1 else 0)
        newest_errors = _tail_json_lines(error_log_path, 1)
        if newest_errors and _RATE_LIMIT.search(str(newest_errors[0].get("error", ""))):
            backoff += 120  # a rate limit asks for breathing room, not just a retry
        print(f"supervisor: benign transient on {str(cell)[-16:]} "
              f"(same-cell {same_cell_count}, resume {resumes}/{MAX_RESUMES}, "
              f"uncertain ${uncertain:.3f}); resuming in {backoff}s", flush=True)
        time.sleep(backoff)


if __name__ == "__main__":
    raise SystemExit(main())
