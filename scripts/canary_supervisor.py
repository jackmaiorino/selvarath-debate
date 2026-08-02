"""Signature-gated auto-resume supervisor for the live canary run.

The strict client halts the whole run on any call whose billing outcome is ambiguous
(UnknownChargeHalt), which is correct accounting but turns a provider's bad day into
dozens of manual resumes. This supervisor automates EXACTLY the reconciliation the
operator performed by hand, and nothing more: it relaunches only when the newest halt
matches the known-benign transient signature, and stops for manual review otherwise.

The benign signature (all conditions required):
- the run exited with halted_reason UnknownChargeHalt and named a halted cell;
- the newest usage-ledger event is an unknown_charge whose error matches the enumerated
  transient set (provider 5xx, 429 rate limit, SDK timeout, truncated stream, or a
  connection reset/abort);
- the newest error-log entry matches that same set and was recorded after this attempt
  started;
- the halted cell has no row in the results file (nothing was half-recorded).

Bounds, all of which stop the supervisor for manual review when crossed:
- at most MAX_RESUMES automatic resumes in one supervisor invocation. This is a
  runaway-loop backstop, not a safety control: the binding safety constraints are the
  same-cell limit and the uncertain-spend ceiling, both unchanged. Raised 60 -> 400 on
  2026-08-01 because Together's checker endpoint halts the run every ~2 cells, so the
  original bound would have stopped the canary with ~300 cells undone;
- at most SAME_CELL_MAX consecutive halts on the same cell (a deterministic failure
  masquerading as a transient, as the gpt-oss streaming regression did);
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
from pathlib import Path

MAX_RESUMES = 400
SAME_CELL_MAX = 3
# Raised 2.00 -> 4.00 on 2026-08-01 by owner instruction (amendment 7). Unlike the resume
# backstop, this IS one of the constraints that bounds real risk, so the amendment records it
# as a deliberate relaxation rather than as housekeeping.
UNCERTAIN_CEILING_USD = 4.00
RESUME_BACKOFF_SECONDS = 60
SAME_CELL_EXTRA_BACKOFF_SECONDS = 240

# The enumerated transient set, grown by recorded amendments 1-5 as Together produced each
# new failure shape: 5xx, 429, SDK timeout, truncated stream, connection reset/abort. Each
# may have billed server-side, which is exactly what the permanently-counted uncertain
# reservation and the uncertain ceiling bound; cell-granular retry cannot duplicate a
# result row. The set stays enumerated so a novel anomaly still stops for manual review.
_BENIGN_TRANSIENT = re.compile(
    r"Error code: 5\d\d|Error code: 429|Request timed out\.|"
    r"streaming response ended without usage chunk|"
    r"Connection reset by peer|Connection aborted|Server disconnected")
_RATE_LIMIT = re.compile(r"Error code: 429")


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
    if outcome.get("halted_reason") not in ("UnknownChargeHalt", "checker_outage"):
        return (f"halt reason {outcome.get('halted_reason')!r} is neither UnknownChargeHalt "
                "nor checker_outage")
    cell = outcome.get("halted_cell_key")
    if not cell:
        return "halt names no cell"
    events = _tail_json_lines(usage_path, 1)
    if not events or events[0].get("status") != "unknown_charge":
        return "newest ledger event is not an unknown_charge"
    if not _BENIGN_TRANSIENT.search(str(events[0].get("error", ""))):
        return ("newest unknown_charge error is not in the enumerated transient set "
                f"(got: {str(events[0].get('error', ''))[:120]!r})")
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
        same_cell_count = same_cell_count + 1 if cell == last_cell else 1
        last_cell = cell
        if same_cell_count > SAME_CELL_MAX:
            print(f"supervisor: STOP cell {cell} halted {same_cell_count} consecutive "
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
