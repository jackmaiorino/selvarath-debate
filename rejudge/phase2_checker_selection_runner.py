"""Checker-candidate selection runner (design: phase2_checker_validation_design_2026-07-18).

Evaluates the four frozen candidate models on the 350-item adopted ground-truth
label set (200 primary + 150 reserve top-up) with the frozen checker prompt at
temperature 0, then scores the frozen gates and applies the frozen selection rule.

Fail-closed properties:
- Live calls require an authorization artifact
  (rejudge/phase2_checker_selection_authorization_2026-07-21.json) that binds the
  owner's approval, the oracle-clearance consult, the spend cap, the sha256 of
  every input artifact, and the prepared usage-ledger identity. Any mismatch
  refuses before the first paid call.
- Spend is enforced by rejudge.api_client's reservation ledger under the bound cap
  with strict per-model prices from the frozen schedule.
- Every item must PASS rejudge.query_screen (scoring_scope rule); a violation
  aborts the run rather than silently filtering.
- Output is append-only JSONL keyed (model, item_id); reruns resume by skipping
  completed keys, so an interrupt never double-spends a completed cell.
- --dry-run validates inputs, prompts, and coverage offline and prints the call
  plan; it never constructs a provider client.

Parsing: the gate demands exact lower-case single tokens. strict_token is the raw
completion only if it is exactly "allow"/"reject"/"unresolved"; a tolerant reading
(strip whitespace and one trailing period, lower-case) is recorded for sensitivity
reporting only and plays no part in the gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rejudge import query_screen  # noqa: E402
from rejudge import run_accounting  # noqa: E402

DESIGN_PATH = ROOT / "rejudge" / "phase2_checker_validation_design_2026-07-18.json"
PRIMARY_PATH = ROOT / "rejudge" / "phase2_checker_primary_set_2026-07-18.json"
RESERVE_PATH = ROOT / "rejudge" / "phase2_checker_reserve_pool_2026-07-18.json"
PRIMARY_LABELS_PATH = ROOT / "rejudge" / "phase2_checker_claude_labels_2026-07-20.json"
RESERVE_LABELS_PATH = ROOT / "rejudge" / "phase2_checker_reserve_topup_claude_labels_2026-07-21.json"
AMENDMENT6_PATH = ROOT / "rejudge" / "phase2_checker_design_amendment6_2026-07-21.json"
AUTHORIZATION_PATH = ROOT / "rejudge" / "phase2_checker_selection_authorization_2026-07-21.json"
OUTPUT_PATH = ROOT / "rejudge" / "output" / "checker_selection_calls.jsonl"
REPORT_PATH = ROOT / "rejudge" / "phase2_checker_selection_report_2026-07-21.json"

TOKENS = ("allow", "reject", "unresolved")
MAX_TOKENS = 16  # role-limits v5 query_checker base; api_client floors reasoning models itself
SEED = 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_items_and_labels() -> list[dict]:
    """Merged frozen-order item list with adopted labels plus amendment-6 overrides."""
    primary = _load(PRIMARY_PATH)["items"]
    reserve = _load(RESERVE_PATH)["items"]
    labels: dict[str, str] = {}
    for path in (PRIMARY_LABELS_PATH, RESERVE_LABELS_PATH):
        for item_id, row in _load(path)["labels"].items():
            if item_id in labels:
                raise SystemExit(f"duplicate label for {item_id}")
            labels[item_id] = row["label"]
    amendment = _load(AMENDMENT6_PATH)
    for override in amendment["label_corrections"]["overrides"]:
        item_id = override["item_id"]
        if labels.get(item_id) != override["from"]:
            raise SystemExit(
                f"override {item_id}: sealed label {labels.get(item_id)!r} != "
                f"declared from={override['from']!r}")
        labels[item_id] = override["to"]
    counts = {"allow": 0, "reject": 0, "unresolved": 0}
    for value in labels.values():
        counts[value] += 1
    expected = amendment["label_corrections"]["corrected_operative_counts"]
    if counts != expected:
        raise SystemExit(f"corrected counts {counts} != amendment {expected}")
    merged = []
    for source, items in (("primary", primary), ("reserve", reserve)):
        for it in items:
            item_id = it["item_id"]
            if item_id not in labels:
                raise SystemExit(f"unlabeled item {item_id} in {source} set")
            result = query_screen.screen_query(
                it["raw_query"], it["candidate_a"], it["candidate_b"])
            if not result.allowed:
                raise SystemExit(
                    f"item {item_id} fails the mechanical screen ({result.reasons}); "
                    "scoring_scope forbids scoring it -- aborting, not filtering")
            merged.append({
                "item_id": item_id,
                "source": source,
                "synthetic": bool(it.get("synthetic")),
                "world": it["world"],
                "raw_query": it["raw_query"],
                "candidate_a": it["candidate_a"],
                "candidate_b": it["candidate_b"],
                "label": labels[item_id],
            })
    if len(merged) != 350 or len(labels) != 350:
        raise SystemExit(f"expected 350 labeled items, got {len(merged)}/{len(labels)}")
    return merged


def build_messages(design: dict, item: dict) -> list[dict]:
    prompt = design["candidate_models"]["checker_prompt"]
    user = prompt["user_prompt_template"].format(
        candidate_a=item["candidate_a"], candidate_b=item["candidate_b"],
        query=item["raw_query"])
    return [{"role": "system", "content": prompt["system_prompt"]},
            {"role": "user", "content": user}]


def tolerant(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    return cleaned if cleaned in TOKENS else None


def input_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT)).replace("\\", "/"): _sha256(p)
            for p in (DESIGN_PATH, PRIMARY_PATH, RESERVE_PATH,
                      PRIMARY_LABELS_PATH, RESERVE_LABELS_PATH, AMENDMENT6_PATH)}


def validate_authorization() -> dict:
    if not AUTHORIZATION_PATH.exists():
        raise SystemExit("no authorization artifact; live run refused")
    auth = _load(AUTHORIZATION_PATH)
    if auth.get("execution_authorized") is not True:
        raise SystemExit("authorization artifact does not authorize execution")
    if not auth.get("oracle_clearance", {}).get("cleared"):
        raise SystemExit("oracle clearance not recorded; live run refused")
    if not auth.get("amendment6_owner_approved"):
        raise SystemExit("amendment 6 owner approval not recorded; live run refused")
    expected = auth.get("input_sha256", {})
    actual = input_hashes()
    if expected != actual:
        raise SystemExit(f"input hash mismatch:\nbound={expected}\nactual={actual}")
    if not isinstance(auth.get("approved_cap_usd"), (int, float)) or auth["approved_cap_usd"] <= 0:
        raise SystemExit("authorization lacks a positive approved_cap_usd")
    if not isinstance(auth.get("ledger"), dict):
        raise SystemExit("authorization lacks the prepared ledger binding")
    return auth


def read_completed() -> set[tuple[str, str]]:
    done = set()
    if OUTPUT_PATH.exists():
        with OUTPUT_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    done.add((row["model"], row["item_id"]))
    return done


def run(dry_run: bool) -> None:
    design = _load(DESIGN_PATH)
    pool = list(design["candidate_models"]["pool"])
    temperature = design["candidate_models"]["temperature"]
    items = load_items_and_labels()
    plan = [(m, it) for m in pool for it in items]
    done = read_completed()
    todo = [(m, it) for m, it in plan if (m, it["item_id"]) not in done]
    print(f"plan {len(plan)} calls; {len(done)} already recorded; {len(todo)} to run")

    if dry_run:
        for m, it in plan[:2]:
            print(json.dumps(build_messages(design, it))[:400])
        print("dry run only; no client constructed")
        return

    auth = validate_authorization()
    schedule = run_accounting.load_price_schedule()
    prices = run_accounting.select_model_prices(schedule, pool)
    usage_log = run_accounting.usage_log_path_for(OUTPUT_PATH)
    client, summary = run_accounting.create_accounted_client(
        approved_cap_usd=float(auth["approved_cap_usd"]), dry_run=False,
        model_prices=prices, usage_log_path=usage_log,
        error_log_path=str(OUTPUT_PATH) + ".errors.jsonl",
        ledger_identity=auth["ledger"]["ledger_identity"])
    print(f"ledger resumed: {summary}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    with OUTPUT_PATH.open("a", encoding="utf-8") as out:
        for model, item in todo:
            raw = client.complete(
                build_messages(design, item), model=model, temperature=temperature,
                seed=SEED, max_tokens=MAX_TOKENS, kind="verdict",
                request_metadata={"run": "checker_selection", "item_id": item["item_id"]})
            row = {
                "model": model,
                "item_id": item["item_id"],
                "label": item["label"],
                "raw": raw,
                "strict_token": raw if raw in TOKENS else None,
                "tolerant_token": tolerant(raw),
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            completed += 1
            if completed % 100 == 0:
                print(f"{completed}/{len(todo)} done, spent ${client.spent_usd():.4f}")
    print(f"run complete: {completed} new calls, accounted spend ${client.spent_usd():.4f}")


def score() -> None:
    design = _load(DESIGN_PATH)
    pool = list(design["candidate_models"]["pool"])
    items = {it["item_id"]: it for it in load_items_and_labels()}
    rows = []
    with OUTPUT_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    by_model: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_model.setdefault(row["model"], {})[row["item_id"]] = row

    amendment = _load(AMENDMENT6_PATH)
    snapshot = _load(ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json")

    def recall(rows_for, label):
        subset = [r for r in rows_for if r["label"] == label]
        if not subset:
            return None, 0
        hit = sum(1 for r in subset if r["strict_token"] == label)
        return hit / len(subset), len(subset)

    report = {
        "models": {},
        "unresolved_gate_disposition": amendment["unresolved_gate_disposition"],
        "mandatory_caveat": amendment["gate_renaming_and_caveat"]["mandatory_caveat"],
    }
    eligible = []
    for model in pool:
        got = by_model.get(model, {})
        if set(got) != set(items):
            report["models"][model] = {"status": f"incomplete: {len(got)}/{len(items)}"}
            continue
        rows_for = [got[i] for i in items]
        for r in rows_for:
            r["label"] = items[r["item_id"]]["label"]
        parseable = sum(1 for r in rows_for if r["strict_token"] is not None) / len(rows_for)
        allow_r, allow_n = recall(rows_for, "allow")
        reject_r, reject_n = recall(rows_for, "reject")
        unres_r, unres_n = recall(rows_for, "unresolved")
        false_allows = [r["item_id"] for r in rows_for
                        if r["label"] in ("reject", "unresolved") and r["strict_token"] == "allow"]
        # Amendment 6: unresolved support is 0, its recall is N/A (never pass/fail/imputed),
        # and the selection metric is the unweighted mean over the observed classes.
        present = [r for r in (allow_r, reject_r, unres_r) if r is not None]
        macro = sum(present) / len(present)
        gates = {
            "parseable": parseable == 1.0,
            "allow_recall": allow_r is not None and allow_r >= 0.95,
            "reject_recall": reject_r is not None and reject_r >= 0.95,
            "zero_benchmark_false_allows": not false_allows,
        }
        subsets = {}
        for name, pred in (("real", lambda it: not it["synthetic"]),
                           ("synthetic", lambda it: it["synthetic"])):
            sub = [r for r in rows_for if pred(items[r["item_id"]])]
            subsets[name] = {
                "n": len(sub),
                "parseable": sum(1 for r in sub if r["strict_token"] is not None) / len(sub),
                "allow_recall": recall(sub, "allow")[0],
                "reject_recall": recall(sub, "reject")[0],
                "false_allows": sum(1 for r in sub if r["label"] in ("reject", "unresolved")
                                    and r["strict_token"] == "allow"),
            }
        entry = {
            "parseable_rate": parseable,
            "allow_recall": allow_r, "allow_n": allow_n,
            "reject_recall": reject_r, "reject_n": reject_n,
            "unresolved_recall": "N/A", "unresolved_support": unres_n,
            "false_allow_item_ids": false_allows,
            "two_class_mean_recall": macro,
            "gates": gates,
            "eligible": all(gates.values()),
            "subsets": subsets,
        }
        report["models"][model] = entry
        if entry["eligible"]:
            p = snapshot["models"][model]
            cost_per_call = p["input_usd_per_million_tokens"]  # proxy: identical prompt lengths
            eligible.append((-macro, -(allow_r or 0.0), cost_per_call, model))
    if eligible:
        eligible.sort()
        report["selected_model"] = eligible[0][3]
    else:
        report["selected_model"] = None
        report["halt"] = "every candidate failed at least one gate; no checker call is authorized"
    report["selection_rule"] = design["selection_rule"]
    report["selection_metric_replacement"] = amendment["selection_metric_replacement"]
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({m: {"eligible": v.get("eligible"),
                          "two_class_mean": v.get("two_class_mean_recall"),
                          "false_allows": len(v.get("false_allow_item_ids", []))}
                      for m, v in report["models"].items()}, indent=1))
    print("selected:", report["selected_model"])
    print("wrote", REPORT_PATH)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    if args.score_only:
        score()
        return
    run(dry_run=args.dry_run)
    if not args.dry_run:
        score()


if __name__ == "__main__":
    main()
