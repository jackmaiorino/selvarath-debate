"""Calibration judging runner for the phase-2 calibration.

Judges the generated calibration transcripts (`rejudge/debate_gen.py` output,
`rejudge/output/calibration_transcripts.jsonl`) against the frozen judge roster
(`rejudge/output/calibration_models.json`). Three cell groups, per the calibration plan:

  b0       all four judges, every transcript, budget 0, K=2 MIRRORED. Replicate 0 uses the
           design A/B assignment (`analysis.infra.design.position_a_is_correct`); replicate 1
           uses its exact negation -- the side-bias diagnostic.
  b2smoke  the three non-anchor judges, the first 12 capped3 transcripts from the 70B
           (Llama) debater sorted by (question_id, transcript_index), budget 2, K=1
           (design assignment). Exercises the full oracle loop per judge.
  b2sat    the anchor judge only, all capped3 transcripts (both debaters), budget 2, K=1.

Disambiguation: transcript_index collides across protocol x debater (four distinct
transcripts can share (question_id, 0)). Every judged cell uses a per-(protocol, debater)
ArmSpec named `cal-{protocol}-{deb}` (deb: "l70" Llama debater, "qplus" Qwen3.7-Plus), all
with strict/clean/robust semantics (placebo False) -- this makes cell_key (arm name + qid +
tidx + budget + replicate, built by `records.build_record`) unambiguous. Each record also
carries `protocol`, `debater_model` (both copied off the source transcript) and `mirrored`
(True iff replicate 1), added after `build_record` returns. Judges are further disambiguated
by writing to a per-judge output file, `calibration_judgments_{judgeshort}.jsonl`, where
judgeshort maps low_primary/low_fallback/anchor/top -> low9/low7/a70/top (JUDGE_SHORT below).
Resume is per file and uses the runner's strict completion audit: malformed tails,
duplicate keys, and keys outside the manifested grid block further calls until repaired.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from analysis.infra.design import position_a_is_correct
from rejudge import judge_loop, records
from rejudge.api_client import (AccountingInvariantError, CapExceededError,
                                UsageLedgerError)
from rejudge.config import ArmSpec, load_protocol
from rejudge.run_accounting import (PriceScheduleError, create_accounted_client,
                                    load_price_schedule, prepare_usage_ledger,
                                    pricing_identity,
                                    select_model_prices,
                                    usage_ledger_generated_paths)
from rejudge.run_manifest import (RunManifestError, ensure_run_manifest,
                                  manifest_path_for)
from rejudge.runner import (OUTPUT_PERSISTENCE_EXIT, REPO_ROOT,
                            OutputPersistenceError, _world_documents,
                            _world_source_files, acquire_output_locks,
                            append_jsonl_record, audit_jsonl_completion,
                            capture_source_hashes, load_done_keys,
                            prepare_jsonl_output,
                            require_manifest_source_snapshot,
                            require_unique_planned_cell_keys,
                            require_unchanged_source_snapshot)

DEFAULT_TRANSCRIPTS = str(REPO_ROOT / "rejudge" / "output" / "calibration_transcripts.jsonl")
DEFAULT_MODELS = str(REPO_ROOT / "rejudge" / "output" / "calibration_models.json")
DEFAULT_OUT_DIR = str(REPO_ROOT / "rejudge" / "output")

# judge roster key -> short code used in the per-judge output filename.
JUDGE_SHORT = {"low_primary": "low9", "low_fallback": "low7", "anchor": "a70", "top": "top",
               "mid_gemma": "g31", "top_oss": "oss120"}
NON_ANCHOR_JUDGES = ("low_primary", "low_fallback", "top")
ALL_CELL_GROUPS = ("b0", "b2smoke", "b2sat")
B2_SMOKE_N = 12
B2_BUDGET = 2


def debater_short(model_id: str) -> str:
    """Map a debater model id to its short code ("l70" Llama, "qplus" Qwen3.7-Plus).

    Raises ValueError on an unrecognized model id (protocol-drift guard, matching
    `rejudge.config.clean_query_phase_prompt`'s anchor-line pattern): the calibration
    debater roster is fixed to exactly these two models, so any other id here means the
    caller is pointing at the wrong data.
    """
    low = model_id.lower()
    if "llama" in low:
        return "l70"
    if "qwen3.7-plus" in low.replace(" ", ""):
        return "qplus"
    raise ValueError(f"unrecognized calibration debater model: {model_id!r}")


def find_debater_model(models_cfg: dict, short: str) -> str:
    for m in models_cfg["debaters"]:
        if debater_short(m) == short:
            return m
    raise ValueError(f"no calibration debater model found for short code {short!r}")


def cal_arm(protocol_name: str, debater_model: str) -> ArmSpec:
    """The per-(protocol, debater) ArmSpec: strict/clean/robust semantics, never placebo."""
    deb = debater_short(debater_model)
    return ArmSpec(f"cal-{protocol_name}-{deb}", "strict", "clean", "robust", placebo=False)


def load_calibration_models(path: str = DEFAULT_MODELS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_transcripts(path: str = DEFAULT_TRANSCRIPTS) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _sort_key(tr: dict):
    return (tr["question_id"], tr["transcript_index"])


def build_cell(judge_key: str, judge_model: str, transcript: dict, budget: int, replicate: int,
              mirrored: bool) -> dict:
    arm = cal_arm(transcript["protocol"], transcript["debater_model"])
    if mirrored:
        design = position_a_is_correct(transcript["question_id"], transcript["transcript_index"])
        position_override = not design
    else:
        position_override = None
    qid, tidx = transcript["question_id"], transcript["transcript_index"]
    return {"judge_key": judge_key, "judge_model": judge_model, "transcript": transcript,
            "arm": arm, "budget": budget, "replicate": replicate,
            "position_override": position_override, "mirrored": mirrored,
            "cell_key": f"{arm.name}|{qid}|{tidx}|{budget}|{replicate}"}


def enumerate_b0_cells(transcripts: list[dict], judges: dict) -> list[dict]:
    """All four judges x every transcript x budget 0 x K=2 (replicate 1 mirrored)."""
    cells = []
    for judge_key, judge_model in judges.items():
        for tr in transcripts:
            cells.append(build_cell(judge_key, judge_model, tr, 0, 0, mirrored=False))
            cells.append(build_cell(judge_key, judge_model, tr, 0, 1, mirrored=True))
    return cells


def enumerate_b2smoke_cells(transcripts: list[dict], judges: dict,
                            l70_debater_model: str | None) -> list[dict]:
    """Three non-anchor judges x first 12 capped3/70B-debater transcripts x budget 2 x K=1."""
    pool = sorted((tr for tr in transcripts if tr["protocol"] == "capped3"
                   and tr["debater_model"] == l70_debater_model), key=_sort_key)[:B2_SMOKE_N]
    cells = []
    for judge_key, judge_model in judges.items():
        if judge_key == "anchor":
            continue
        for tr in pool:
            cells.append(build_cell(judge_key, judge_model, tr, B2_BUDGET, 0, mirrored=False))
    return cells


def enumerate_b2sat_cells(transcripts: list[dict], judges: dict) -> list[dict]:
    """Anchor judge only x all capped3 transcripts (both debaters) x budget 2 x K=1."""
    if "anchor" not in judges:
        return []
    pool = [tr for tr in transcripts if tr["protocol"] == "capped3"]
    judge_model = judges["anchor"]
    return [build_cell("anchor", judge_model, tr, B2_BUDGET, 0, mirrored=False) for tr in pool]


def enumerate_cells(cell_groups, transcripts: list[dict], judges: dict,
                    l70_debater_model: str | None) -> list[dict]:
    cells: list[dict] = []
    if "b0" in cell_groups:
        cells += enumerate_b0_cells(transcripts, judges)
    if "b2smoke" in cell_groups:
        cells += enumerate_b2smoke_cells(transcripts, judges, l70_debater_model)
    if "b2sat" in cell_groups:
        cells += enumerate_b2sat_cells(transcripts, judges)
    return cells


def judge_cell(cell: dict, client, exp_protocol: dict, world_document: str) -> dict:
    """Run one judgment and inject the extra disambiguation fields onto the record."""
    tr = cell["transcript"]
    rec = judge_loop.run_judgment(
        tr, world_document, cell["arm"], cell["budget"], cell["replicate"], client,
        exp_protocol, cell["judge_model"], position_override=cell["position_override"])
    rec["protocol"] = tr["protocol"]
    rec["debater_model"] = tr["debater_model"]
    rec["mirrored"] = cell["mirrored"]
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--judges", default="all")
    ap.add_argument("--cells", default=",".join(ALL_CELL_GROUPS))
    ap.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS)
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument(
        "--cell-key-file", default=None,
        help="Optional JSON array selecting an exact manifested supplement cell set.")
    args = ap.parse_args(argv)

    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD (spend policy).", file=sys.stderr)
        return 2

    cell_groups = [c.strip() for c in args.cells.split(",") if c.strip()]
    unknown_cells = [c for c in cell_groups if c not in ALL_CELL_GROUPS]
    if unknown_cells:
        print(f"unknown cell groups: {unknown_cells}", file=sys.stderr)
        return 2

    world_source_files = _world_source_files()
    source_files = {
        "experiment_protocol": REPO_ROOT / "experiment_protocol.json",
        "calibration_transcripts": Path(args.transcripts).resolve(),
        "calibration_models": Path(args.models).resolve(),
        **({"cell_key_selection": Path(args.cell_key_file).resolve()}
           if args.cell_key_file else {}),
        **world_source_files,
    }
    try:
        before_load = capture_source_hashes(source_files)
    except RunManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    models_cfg = load_calibration_models(args.models)
    judges_cfg = models_cfg["judges"]

    judge_keys = ([k for k in JUDGE_SHORT if k in judges_cfg] if args.judges == "all"
                 else [j.strip() for j in args.judges.split(",") if j.strip()])
    unknown_judges = [j for j in judge_keys if j not in JUDGE_SHORT]
    if unknown_judges:
        print(f"unknown judges: {unknown_judges}", file=sys.stderr)
        return 2
    missing_judges = [j for j in judge_keys if j not in judges_cfg]
    if missing_judges:
        print(f"judges missing from {args.models}: {missing_judges}", file=sys.stderr)
        return 2
    selected_judges = {k: judges_cfg[k] for k in judge_keys}

    transcripts = load_transcripts(args.transcripts)
    l70_model = find_debater_model(models_cfg, "l70") if "b2smoke" in cell_groups else None
    exp_protocol = load_protocol()
    worlds = _world_documents(world_source_files)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed_path = out_dir / "calibrate_failed_cells.jsonl"
    usage_path = out_dir / "calibration_usage.jsonl"
    try:
        price_schedule = load_price_schedule(args.models)
        model_prices = select_model_prices(
            price_schedule,
            [*selected_judges.values(), exp_protocol["protocol"]["models"]["oracle"]],
        )
    except PriceScheduleError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    cells = enumerate_cells(cell_groups, transcripts, selected_judges, l70_model)
    selected_cell_keys = None
    if args.cell_key_file:
        try:
            selected_cell_keys = json.loads(
                Path(args.cell_key_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(f"REFUSED: could not read --cell-key-file: {exc}", file=sys.stderr)
            return 2
        if (not isinstance(selected_cell_keys, list)
                or not all(isinstance(key, str) and key for key in selected_cell_keys)
                or len(selected_cell_keys) != len(set(selected_cell_keys))):
            print("REFUSED: --cell-key-file must contain a duplicate-free JSON array of "
                  "non-empty cell keys.", file=sys.stderr)
            return 2
        available = {cell["cell_key"] for cell in cells}
        unknown_selected = sorted(set(selected_cell_keys) - available)
        if unknown_selected:
            print(f"REFUSED: selected cell keys are not in the requested judge/cell grid: "
                  f"{unknown_selected}", file=sys.stderr)
            return 2
        wanted = set(selected_cell_keys)
        cells = [cell for cell in cells if cell["cell_key"] in wanted]

    try:
        loaded_source_hashes = require_unchanged_source_snapshot(before_load, source_files)
    except RunManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    out_paths = {jk: out_dir / f"calibration_judgments_{JUDGE_SHORT[jk]}.jsonl" for jk in judge_keys}
    # The calibration ledger is intentionally shared by every judge output in one
    # directory. A later invocation may select a disjoint judge subset, so ledger
    # binding and generated-file exclusions must consider the complete known output
    # family rather than only this invocation's selected files.
    all_calibration_outputs = [
        out_dir / f"calibration_judgments_{short}.jsonl"
        for short in JUDGE_SHORT.values()
    ]
    expected_by_judge: dict[str, set[str]] = {}
    try:
        for judge_key in judge_keys:
            expected_by_judge[judge_key] = require_unique_planned_cell_keys(
                (cell["cell_key"] for cell in cells if cell["judge_key"] == judge_key),
                label=f"calibration grid for judge {judge_key}",
            )
    except RunManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    stack = ExitStack()
    try:
        # Multiple calibration writers must acquire locks in a globally stable order so
        # overlapping invocations cannot deadlock while each owns a different judge file.
        acquire_output_locks(
            stack,
            list(out_paths.values()) if args.dry_run
            else [*out_paths.values(), usage_path],
        )
        manifest_preexists = any(
            manifest_path_for(out_path).exists()
            for out_path in all_calibration_outputs)
        ledger_identity = (None if args.dry_run else prepare_usage_ledger(
            usage_path, allow_create=not manifest_preexists))
        calibration_generated_paths = [
            *usage_ledger_generated_paths(usage_path),
            failed_path,
            out_dir / "calibrate_errors.jsonl",
        ]
        for other_output in all_calibration_outputs:
            calibration_generated_paths.extend([
                other_output,
                manifest_path_for(other_output),
                other_output.with_name(f"{other_output.name}.lock"),
            ])

        common_identity = {
            "run_kind": "calibration-judging",
            "dry_run": args.dry_run,
            "models": {
                "judges": selected_judges,
                "oracle": exp_protocol["protocol"]["models"]["oracle"],
                "debater_selection_roster": models_cfg["debaters"],
            },
            "prices": pricing_identity(price_schedule, model_prices),
            "protocol_content": {
                "experiment_protocol": exp_protocol,
                "cell_groups": cell_groups,
                "b0": {"budget": 0, "replicates": 2, "replicate_1_mirrored": True},
                "b2smoke": {"budget": B2_BUDGET, "per_judge_transcripts": B2_SMOKE_N},
                "b2sat": {"budget": B2_BUDGET, "protocol": "capped3"},
                "arm_semantics": {"oracle_normalizer": "strict", "composer": "clean",
                                  "done_detector": "robust", "placebo": False},
            },
            "source_files": source_files,
            "generated_paths": calibration_generated_paths,
            "cli_params": {
                "approved_cap_usd": args.approved_cap,
                "dry_run": args.dry_run,
                "workers": args.workers,
                "judges": judge_keys,
                "cells": cell_groups,
                "transcripts": str(Path(args.transcripts).resolve()),
                "models": str(Path(args.models).resolve()),
                "out_dir": str(out_dir.resolve()),
                "usage_log": str(usage_path.resolve()),
                "usage_ledger_identity": ledger_identity,
                "cell_key_file": (str(Path(args.cell_key_file).resolve())
                                  if args.cell_key_file else None),
            },
        }
        # All locks are already held. Creating every adjacent manifest only after that
        # guarantees the multi-output invocation has one exclusive resume snapshot.
        for out_path in sorted(out_paths.values(), key=lambda p: str(p.resolve()).casefold()):
            manifest = ensure_run_manifest(out_path, repo_root=REPO_ROOT, **common_identity)
            require_manifest_source_snapshot(manifest, loaded_source_hashes)
    except (RunManifestError, UsageLedgerError) as exc:
        stack.close()
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    try:
        try:
            for out_path in out_paths.values():
                prepare_jsonl_output(out_path)
            done = {
                judge_key: audit_jsonl_completion(
                    out_paths[judge_key], expected_by_judge[judge_key])
                for judge_key in judge_keys
            }
        except OutputPersistenceError as exc:
            print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
            return OUTPUT_PERSISTENCE_EXIT
        todo = [c for c in cells if c["cell_key"] not in done[c["judge_key"]]]
        print(f"{len(cells)} cells, {len(cells) - len(todo)} done, {len(todo)} to run "
              f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

        try:
            client, prior_usage = create_accounted_client(
                approved_cap_usd=args.approved_cap or 0.0,
                dry_run=args.dry_run,
                model_prices=model_prices,
                usage_log_path=usage_path,
                error_log_path=out_dir / "calibrate_errors.jsonl",
                ledger_identity=ledger_identity,
            )
        except (OSError, ValueError, UsageLedgerError) as exc:
            print(f"REFUSED: could not establish cumulative usage ledger: {exc}",
                  file=sys.stderr)
            return 2
        if prior_usage["events"]:
            print(f"prior accounted spend: ${prior_usage['accounted_spend_usd']:.4f} "
                  f"across {prior_usage['events']} ledger events")
        lock = threading.Lock()
        cap_hit = threading.Event()
        accounting_failed = threading.Event()
        output_failed = threading.Event()

        def run_cell(cell):
            if cap_hit.is_set() or accounting_failed.is_set() or output_failed.is_set():
                return
            tr = cell["transcript"]
            try:
                rec = judge_cell(cell, client, exp_protocol, worlds[tr["world"]])
            except CapExceededError:
                cap_hit.set()
                return
            except (UsageLedgerError, AccountingInvariantError) as exc:
                accounting_failed.set()
                print(f"ACCOUNTING UNSAFE: {exc}", file=sys.stderr)
                return
            except Exception as exc:
                with lock:
                    with open(failed_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"cell_key": cell["cell_key"],
                                            "judge": cell["judge_key"], "error": str(exc),
                                            "ts": records.utc_now_iso()}) + "\n")
                print(f"WARN: cell {cell['cell_key']} ({cell['judge_key']}) failed: {exc}",
                      file=sys.stderr)
                return
            try:
                with lock:
                    append_jsonl_record(out_paths[cell["judge_key"]], rec)
            except OutputPersistenceError as exc:
                output_failed.set()
                print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
                return

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_cell, todo))

        completed: set[tuple[str, str]] = set()
        for judge_key, out_path in out_paths.items():
            try:
                completed.update(
                    (judge_key, cell_key) for cell_key in audit_jsonl_completion(
                        out_path, expected_by_judge[judge_key]))
            except OutputPersistenceError as exc:
                output_failed.set()
                print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
        expected = {(judge_key, cell_key)
                    for judge_key, keys in expected_by_judge.items()
                    for cell_key in keys}
        missing = expected - completed
        print(f"completion check: {len(missing)} missing")

        if accounting_failed.is_set():
            print("ACCOUNTING UNSAFE -- reconcile the usage ledger and provider billing "
                  "before resume.", file=sys.stderr)
            return 4
        if output_failed.is_set():
            print("OUTPUT UNSAFE -- repair the manifested output before resume.",
                  file=sys.stderr)
            return OUTPUT_PERSISTENCE_EXIT
        if cap_hit.is_set():
            print("CAP REACHED -- run halted. The manifested ceiling is immutable; reconcile "
                  "spend before authorizing a supplemental run.", file=sys.stderr)
            return 3
        if missing:
            return 1

        print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
        return 0
    finally:
        stack.close()


if __name__ == "__main__":
    raise SystemExit(main())
