"""Unpaid saved-response diagnosis, exact reservations, backups and replay.

No provider clients are created. Proposed token controls are serialized only.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import authoring as A, cli, expansion as E, preflight, store, validation as V
from finalphase.models import spec
from finalphase.prompts_authoring import TASK_TYPES
from finalphase.providers import Request


def ledger(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {t: [dict(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                for t in ("calls", "batches", "events")}


def saved_request(row: dict) -> Request:
    return Request(**store._req_fields(json.loads(row["request"])))


def diagnosis(worlds: list[dict], data: dict) -> dict:
    _, canonical = V.workload(worlds)
    ids = {r.custom_id for r in canonical}
    questions = {A.qid(w["world_id"], i): {**q, "author": w["author"]}
                 for w in worlds for i, q in enumerate(w["questions"])}
    counts = {k: Counter() for k in ("author", "answer_order", "task_type")}
    truncated = []
    parsing, disagreements, fact_failures = [], [], []
    statuses = Counter()
    for row in data["calls"]:
        if row["custom_id"] not in ids:
            continue
        response = json.loads(row["response"])
        statuses[response["status"]] += 1
        request = saved_request(row)
        qid = request.custom_id.split(":")[1]
        q = questions[qid]
        if response["status"] == "truncated":
            raw = response["extra"]["raw_response"]
            choice = raw["choices"][0]
            message = choice["message"]
            item = {"custom_id": request.custom_id, "question_id": qid, "author": q["author"],
                    "answer_order": request.custom_id.rsplit(":", 1)[1], "task_type": q["task_type"],
                    "max_tokens": request.max_tokens, "input_tokens": response["input_tokens"],
                    "output_tokens": response["output_tokens"], "reasoning_tokens": response["reasoning_tokens"],
                    "finish_reason": choice["finish_reason"], "final_content": message["content"],
                    "reasoning_characters": len(message.get("reasoning_content") or ""),
                    "raw_usage": raw["usage"], "raw_sha256": E.digest(json.dumps(raw, sort_keys=True).encode()),
                    "classification": "output_limit_exhausted_partial_final" if message["content"] else "output_limit_exhausted_before_final_answer"}
            assert item["finish_reason"] == "length" and item["output_tokens"] == item["max_tokens"] == 4000
            assert 0 <= item["reasoning_tokens"] <= 4000
            truncated.append(item)
            for field in counts:
                counts[field][item[field]] += 1
        elif response["status"] == "ok":
            if request.custom_id.startswith("validate:"):
                parsed = A.parse_validation(response["text"])
                if (parsed["answer"] not in ("A", "B") or parsed["other_equally_defensible"] not in ("yes", "no")
                        or parsed["reversing_reading"] not in ("yes", "no")):
                    parsing.append(request.custom_id)
                elif parsed["answer"] != ("A" if request.custom_id.endswith("key_a") else "B") or parsed["other_equally_defensible"] == "yes":
                    disagreements.append({"custom_id": request.custom_id, "parsed": parsed})
            else:
                answer = A.parse_oracle(response["text"])
                if answer == "INVALID":
                    parsing.append(request.custom_id)
                elif answer != "YES":
                    fact_failures.append({"custom_id": request.custom_id, "answer": answer, "text": response["text"]})
    exposures = {k: Counter() for k in counts}
    for request in canonical:
        if request.model != "dspro" or not request.custom_id.startswith("validate:"):
            continue
        q = questions[request.custom_id.split(":")[1]]
        for field, value in (("author", q["author"]), ("answer_order", request.custom_id.rsplit(":", 1)[1]), ("task_type", q["task_type"])):
            exposures[field][value] += 1
    original_transport = [event for event in data["events"] if "503" in event["detail"]]
    return {"canonical_statuses": dict(statuses), "truncated_calls": truncated,
            "truncated_questions": len({x["question_id"] for x in truncated}),
            "truncations_by": {k: dict(v) for k, v in counts.items()},
            "key_check_exposures_by": {k: dict(v) for k, v in exposures.items()},
            "ok_response_parse_failures": parsing, "completed_key_disagreements": disagreements,
            "substantive_fact_failures": fact_failures, "saved_original_503_events": original_transport,
            "unresolved_transport_rows": [r["custom_id"] for r in data["calls"] if r["status"] not in store.MEASURED],
            "note": "Frozen no_response labels include output truncations. Reasoning is not salvaged as a final answer. Two original 503s were separately resolved; their saved errors and billing reserve remain."}


def token_controls(root: Path, worlds: list[dict]) -> list[Request]:
    rows = [json.loads(line) for line in (root / "bench/validation.jsonl").read_text(encoding="utf-8").splitlines()]
    selected = []
    for author in ("fable", "astra"):
        for task in TASK_TYPES:
            candidates = [r for r in rows if r["retained"] and r["author"] == author and r["task_type"] == task]
            if not candidates:
                raise ValueError(f"no retained control for {author}/{task}")
            selected.append(min(candidates, key=lambda r: E.digest(f"{E.TOKEN_PROPOSAL}:{r['question_id']}".encode()))["question_id"])
    _, canonical = V.workload(worlds)
    sample = [r for r in canonical if r.model == "dspro" and r.custom_id.startswith("validate:") and r.custom_id.split(":")[1] in selected]
    return [replace(r, custom_id=f"qualification:{E.TOKEN_PROPOSAL}:{arm}:{r.custom_id}", max_tokens=E.PROPOSED_DSPRO_KEY_TOKENS)
            for arm in ("serial", "parallel") for r in sample]


def costs(root: Path, worlds: list[dict], authors: dict, validators: dict, controls: list[Request]) -> dict:
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    original = json.loads((root / "provider_funding_plan.json").read_text(encoding="utf-8"))
    completion = json.loads((root / "validation_completion_receipt.json").read_text(encoding="utf-8"))
    money = lambda value: Decimal(str(value)).quantize(Decimal("0.00000001"))
    recent = [r for r in authors["calls"] if ":length-v2" in r["custom_id"] and r["status"] in store.MEASURED]
    fable = sum(Decimal(str(r["cost"])) for r in recent)
    astra = sum(Decimal(str(r["cost"])) for r in authors["calls"] if r["model"] == "astra")
    author_expected = {"anthropic": fable * 19, "openai": astra * 19}
    successful_fable = [r for r in recent if r["status"] == "ok"]
    author_no_retry = {"anthropic": sum(Decimal(str(r["cost"])) for r in successful_fable) * 19, "openai": astra * 19}
    author_reqs = E.author_requests(root)
    author_max = {p: sum((E.maximum(r, True) for r in author_reqs if spec(r.model).provider == p), Decimal(0)) for p in author_expected}
    # Qualification has two matched calls per author, serial controls plus parallel controls.
    author_controls = E.author_qualification_controls()
    author_qual = {p: sum((E.maximum(r, True) for r in author_controls if spec(r.model).provider == p), Decimal(0)) for p in author_expected}
    _, requests = V.workload(worlds)
    v1_max = {p: sum((E.maximum(r, True) for r in requests if spec(r.model).provider == p), Decimal(0)) * 19 for p in ("anthropic", "openai", "together")}
    v2 = [replace(r, max_tokens=E.PROPOSED_DSPRO_KEY_TOKENS) if r.model == "dspro" and r.custom_id.startswith("validate:") else r for r in requests]
    v2_max = {p: sum((E.maximum(r, True) for r in v2 if spec(r.model).provider == p), Decimal(0)) * 19 for p in v1_max}
    canonical_observed = {p: sum(money(r["cost"]) for r in validators["calls"] if spec(r["model"]).provider == p
                               and not r["custom_id"].startswith("qualification:")) * 19 for p in v1_max}
    # The two paid transport rows are included exactly once; canonical aliases cost zero.
    token_qual = sum((E.maximum(r, False) for r in controls), Decimal(0))
    extra_tokens = sum(1 for r in validators["calls"] if r["status"] == "truncated") * (12000 - 4000)
    repaired_tail = Decimal(extra_tokens) * Decimal("3.96") / 1000000 * 19
    validation_stress = {**canonical_observed, "together": canonical_observed["together"] + repaired_tail}
    balances = original["incremental_plan"]["estimated_balances_after_observed_validation_and_503_reserve_usd"]
    recent_max_by_provider = {}
    # Future validation texts do not exist. Freeze this UTF-8 envelope and stop before dispatch if any future body exceeds it.
    for author in ("fable", "astra"):
        for role in ("frontier_key", "dspro_key", "fact"):
            subset = [r for r in v2 if ((r.custom_id.startswith("factcheck:") if role == "fact" else r.custom_id.startswith("validate:"))
                      and ((r.model == "dspro") if role != "frontier_key" else r.model != "dspro"))
                      and next(w["author"] for w in worlds if w["world_id"] == r.custom_id.split(":")[1].split("-Q")[0]) == author]
            recent_max_by_provider[f"{author}:{role}"] = max(E.maximum(r, True) for r in subset)
    # All 12 candidates eligible, five facts each, 76 future worlds per author.
    future_hard = {p: Decimal(0) for p in v1_max}
    for author in ("fable", "astra"):
        frontier_provider = "openai" if author == "fable" else "anthropic"
        future_hard[frontier_provider] += 76 * 12 * 2 * recent_max_by_provider[f"{author}:frontier_key"]
        future_hard["together"] += 76 * 12 * (2 * recent_max_by_provider[f"{author}:dspro_key"] + 5 * recent_max_by_provider[f"{author}:fact"])
    # Explicit maximum: one separately journaled exact-body transport retry per future request, plus uncertain original billing.
    future_retry = dict(future_hard)
    future_qualification_extra = 4 * max(recent_max_by_provider[f"{a}:dspro_key"] for a in ("fable", "astra")) + 4 * max(recent_max_by_provider[f"{a}:fact"] for a in ("fable", "astra"))
    incremental = {p: author_max.get(p, Decimal(0)) + author_qual.get(p, Decimal(0)) + future_hard[p] + future_retry[p]
                   + (token_qual + future_qualification_extra if p == "together" else Decimal(0)) for p in v1_max}
    ledger_used = money(completion["conservative_campaign_cost_bound_usd"])
    auth_total = sum((Decimal(str(r["cost"])) for r in authors["calls"]), Decimal(0))
    author_cap_need = auth_total + sum(author_max.values()) + sum(author_qual.values())
    validation_cap_need = Decimal(str(completion["conservative_stage_cost_bound_usd"])) + 2 * sum(future_hard.values()) + token_qual + future_qualification_extra
    alloc = {"author": int(author_cap_need.to_integral_value(rounding="ROUND_CEILING")),
             "validate": int(validation_cap_need.to_integral_value(rounding="ROUND_CEILING")), "canary": 60, "pilot": 650, "reserve": 590}
    alloc["main"] = 6000 - sum(alloc.values())
    later_high = original["high"]["stages"]
    high_alloc = {**alloc, "canary": 131, "pilot": 1365}
    high_alloc["main"] = 6000 - sum(v for k, v in high_alloc.items() if k != "main")
    proposal_total = ledger_used + sum(incremental.values())
    return {"frozen_ceiling_usd": 6000, "existing_campaign_bound_usd": ledger_used,
            "remaining_worlds": 152, "remaining_by_author": 76,
            "remaining_author_no_retry_observed_usd": author_no_retry,
            "remaining_author_recent_retry_observed_usd": author_expected,
            "remaining_author_two_generation_maximum_usd": author_max,
            "author_qualification_maximum_usd": author_qual,
            "author_qualification_requests": [asdict(r) for r in author_controls],
            "remaining_validation_observed_receipt_extrapolation_usd": canonical_observed,
            "remaining_validation_v2_tail_stress_usd": validation_stress,
            "remaining_validation_v1_19x_saved_body_reservations_usd": v1_max,
            "remaining_validation_v2_19x_saved_body_reservations_usd": v2_max,
            "future_validation_per_request_maximum_usd": recent_max_by_provider,
            "remaining_validation_v2_full_12_questions_5_facts_envelope_usd": future_hard,
            "future_exact_body_transport_retry_reserve_usd": future_retry,
            "future_validation_qualification_extra_maximum_usd": future_qualification_extra,
            "token_qualification_maximum_usd": token_qual,
            "maximum_incremental_preparation_by_provider_usd": incremental,
            "maximum_incremental_preparation_usd": sum(incremental.values()),
            "maximum_cumulative_preparation_usd": proposal_total,
            "proposed_author_allocation_need_usd": author_cap_need,
            "proposed_validation_allocation_need_usd": validation_cap_need,
            "proposed_allocations_round_up_usd": alloc,
            "proposed_allocations_with_later_high_forecast_usd": high_alloc,
            "later_canary_high_forecast_usd": sum(money(n) for n in later_high["debater canary"].values()),
            "later_pilot_including_oracle_high_forecast_usd": sum(money(n) for n in later_high["pilot"].values()) + sum(money(n) for n in later_high["oracle selection"].values()),
            "provider_balances_ledger_estimates_usd": balances,
            "funding_gaps_against_full_preparation_maximum_usd": {p: max(Decimal(0), incremental[p] - Decimal(str(balances[p]))) for p in incremental},
            "openai_known_monthly_used_usd": Decimal(str(original["incremental_plan"]["openai_known_monthly_used_usd"])),
            "openai_reported_setting_usd": 120,
            "openai_minimum_total_monthly_capacity_for_package_usd": Decimal(str(original["incremental_plan"]["openai_known_monthly_used_usd"])) + incremental["openai"],
            "openai_approved_org_capacity_confirmed": False, "openai_project_capacity_confirmed": False,
            "assumptions": ["Frozen rates, batch frontier authors/validators, live Together; no cache discounts in reservations.",
                "Observed recent Fable: six length-v2 paid generations for four accepted worlds; Astra: four paid generations for four worlds.",
                "19x extrapolations are forecasts from eight worlds, not an exact reservation for unknown future texts.",
                "Hard validation envelope uses maximum saved request cost within each author/role and allows every question five facts; future bodies exceeding it stop before registration.",
                "One future transport retry reserved per request, separately authorized; no automatic retry of truncation or scientific rejection. Eight future serial validation qualification controls are additionally reserved; canonical probes are already included.",
                "Author qualification uses synthetic control identities; no controls promoted. Complete author cohort retains two generation attempts.",
                "Stage allocations above are a proposal only. Existing $200/$200/$60/$650/$4300 and $590 reserve are unchanged.",
                "Balances are saved user reports minus this campaign's ledger, not verified current balances; unrelated usage and approved provider limits remain unknown."]}


def replay(root: Path, data: dict) -> dict:
    before = E.digest((root / "validate.db").read_bytes())
    results = (root / "bench/validation.jsonl").read_bytes()
    dispatches = []
    def forbidden(*args, **kwargs):
        dispatches.append(True)
        raise RuntimeError("provider dispatch forbidden during replay")
    live, batch = store.LIVE.copy(), store.BATCH.copy()
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        shutil.copytree(root / "bench", target / "bench")
        shutil.copy2(root / "run_manifest.json", target / "run_manifest.json")
        with closing(sqlite3.connect((root / "validate.db").as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target / "validate.db")) as destination:
                source.backup(destination)
        store.LIVE.update({p: forbidden for p in live})
        store.BATCH.update({p: (forbidden, forbidden, forbidden) for p in batch})
        old_root, old_bench = cli.RUN_ROOT, cli.BENCH
        try:
            s = store.Store(target / "validate.db", "validate", 200, max_transport_attempts=1)
            try:
                responses = s.run([saved_request(r) for r in data["calls"]], mode="batch", allow_live_fallback=False)
                assert len(responses) == 767
                assert all(asdict(responses[r["custom_id"]]) == json.loads(r["response"]) for r in data["calls"])
            finally:
                s.db.close()
            cli.RUN_ROOT, cli.BENCH = target, target / "bench"
            # Replay only: all provider entry points are poisoned; no paid-launch preflight is bypassed.
            cli.cmd_validate(type("ReplayArgs", (), {"workers": 8, "mode": "batch"})())
            assert results == (target / "bench/validation.jsonl").read_bytes()
            assert data == ledger(target / "validate.db")
        finally:
            cli.RUN_ROOT, cli.BENCH = old_root, old_bench
            store.LIVE.update(live); store.BATCH.update(batch)
    after = E.digest((root / "validate.db").read_bytes())
    assert before == after and not dispatches
    return {"provider_dispatches": 0, "cached_responses": 767, "primary_store_before_sha256": before,
            "primary_store_after_sha256": after, "validation_rows_sha256": E.digest(results), "results_bit_identical": True,
            "paid_qualification": "Original receipt preserved; current source changes require compatible qualification before a paid launch."}


def jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def main() -> None:
    root = cli.RUN_ROOT
    preservation = root / "preserved" / "pre-expansion-preparation"
    preservation.mkdir(parents=True, exist_ok=True)
    for name in ("run_manifest.json", "provider_funding_plan.json", "validation_audit_packet.json"):
        if not (preservation / name).exists():
            cli._preserve_bytes(preservation / name, (root / name).read_bytes())
    for name in ("author", "validate"):
        destination_path = preservation / f"{name}.db"
        if not destination_path.exists():
            with closing(sqlite3.connect((root / f"{name}.db").as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(destination_path)) as destination:
                    source.backup(destination)
        assert ledger(root / f"{name}.db") == ledger(destination_path)
    worlds = cli._worlds()
    authors, validators = ledger(root / "author.db"), ledger(root / "validate.db")
    controls = token_controls(root, worlds)
    diagnostic = diagnosis(worlds, validators)
    report = costs(root, worlds, authors, validators, controls)
    payloads = {"validation_truncation_diagnosis.json": diagnostic, "benchmark_expansion_costs.json": report,
                "validation_token_v2_proposal.json": {"version": E.TOKEN_PROPOSAL, "approved": False, "applied": False,
                    "scope": "48 control calls from 12 retained questions, one per author and task type, both answer orders, serial then eight workers",
                    "rejected_questions_retried": False, "retention_contribution": False,
                    "maximum_cost_usd": report["token_qualification_maximum_usd"],
                    "requests": [asdict(r) for r in controls]},
                "expansion_preparation_replay.json": replay(root, validators)}
    from scripts import finalphase_length_correction as correction
    payloads["expansion_preparation_author_replay.json"] = correction.replay(authors["calls"])
    for name, value in payloads.items():
        (root / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=jsonable) + "\n", encoding="utf-8")
    summary = {"truncations": len(diagnostic["truncated_calls"]), "patterns": diagnostic["truncations_by"],
               "exposures": diagnostic["key_check_exposures_by"], "parse_failures": len(diagnostic["ok_response_parse_failures"]),
               "key_disagreements": diagnostic["completed_key_disagreements"], "fact_failures": diagnostic["substantive_fact_failures"],
               "costs": {k: v for k, v in report.items() if k not in ("author_qualification_requests", "assumptions")},
               "audit_complete": False, "paid_requests_dispatched": 0}
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=jsonable))


if __name__ == "__main__":
    main()
