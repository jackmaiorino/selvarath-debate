"""Fake-only tests for the explicit Phase 3 billing evidence inventory."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from rejudge import api_client
from rejudge import phase3_main_billing_evidence_inventory as inventory
from scripts import phase3_main_build_billing_evidence_inventory as cli


WINDOW_START = "2026-08-29T00:00:00Z"
WINDOW_END = "2026-08-30T00:00:00Z"


def _usage_fields(*, attempt_id: str, cost: float) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "model": "fake/model",
        "kind": "judge",
        "seed": 7,
        "attempt": 1,
        "prompt_tokens": None,
        "completion_tokens": None,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "cost_usd": cost,
        "metadata": {"cell": attempt_id},
    }


def _write_ledger(
    path: Path,
    events: list[dict[str, Any]],
    *,
    ledger_id: str = "fake-ledger",
    genesis_ts: str = "2026-08-28T23:00:00+00:00",
) -> Path:
    rows: list[dict[str, Any]] = [{
        "status": "ledger_genesis",
        "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
        "ledger_id": ledger_id,
        "sequence": 0,
        "prev_event_hash": None,
        "ts": genesis_ts,
    }]
    rows[0]["event_hash"] = api_client._usage_event_hash(rows[0])
    for index, event in enumerate(events, 1):
        row = {
            "ts": f"2026-08-29T00:0{index}:00+00:00",
            **event,
            "ledger_id": ledger_id,
            "sequence": index,
            "prev_event_hash": rows[-1]["event_hash"],
        }
        row["event_hash"] = api_client._usage_event_hash(row)
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    state_path = api_client.usage_ledger_state_path(path)
    state_path.write_text(
        json.dumps({
            "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
            "ledger_id": ledger_id,
            "last_sequence": len(rows) - 1,
            "last_event_hash": rows[-1]["event_hash"],
        }, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _settled_and_open_ledger(path: Path) -> Path:
    return _write_ledger(path, [
        {"status": "reserved", **_usage_fields(attempt_id="settled", cost=0.25)},
        {
            "status": "success",
            **_usage_fields(attempt_id="settled", cost=0.20),
            "prompt_tokens": 8,
            "completion_tokens": 9,
            "response_metadata": None,
        },
        {"status": "reserved", **_usage_fields(attempt_id="open", cost=0.30)},
    ])


def _write_aux(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _aux_rows() -> list[dict[str, Any]]:
    return [
        {
            "ts": "2026-08-29T01:00:00Z",
            "probe_id": "p-settled",
            "reserved_usd": 0.04,
            "actual_usd": 0.03,
            "prompt_tokens": 4,
            "completion_tokens": 2,
            "finish_reason": "stop",
        },
        {
            "ts": "2026-08-29T01:01:00Z",
            "probe_id": "p-unknown",
            "attempt": 0,
            "reserved_usd": 0.07,
            "actual_usd": None,
            "error": "fake timeout",
        },
    ]


def _build_mixed(tmp_path: Path) -> dict[str, Any]:
    ledger = _settled_and_open_ledger(tmp_path / "usage.jsonl")
    aux = _write_aux(tmp_path / "screen.jsonl", _aux_rows())
    return inventory.build_inventory(
        window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END,
        ledger_sources=(("ledger-main", ledger),),
        auxiliary_sources=(("aux-screen", aux),),
        project_root=tmp_path,
    )


def test_builds_exact_non_authorizing_mixed_inventory(tmp_path: Path) -> None:
    record = _build_mixed(tmp_path)

    assert record["schema_version"] == inventory.SCHEMA_VERSION
    assert record["selection_basis"] == "explicit_local_source_list"
    assert record["authoritative_completeness"] == "not_established"
    assert record["evidence_only"] is True
    assert record["execution_authorized"] is False
    assert record["provider_calls_authorized"] is False
    assert record["main_run_spend_authorized"] is False
    assert [source["source_id"] for source in record["sources"]] == [
        "aux-screen", "ledger-main"
    ]
    assert record["totals"] == {
        "row_count": 5,
        "first_row_utc": "2026-08-29T00:01:00Z",
        "last_row_utc": "2026-08-29T01:01:00Z",
        "actual_spend_usd": "0.23",
        "uncertain_spend_usd": "0.37",
        "accounted_spend_usd": "0.6",
        "uncertain_line_refs": [
            {"source_id": "aux-screen", "line_number": 2},
            {"source_id": "ledger-main", "line_number": 4},
        ],
    }
    assert inventory.validate_inventory(record, project_root=tmp_path) == {
        "source_count": 2,
        "row_count": 5,
        "actual_spend_usd": "0.23",
        "uncertain_spend_usd": "0.37",
        "accounted_spend_usd": "0.6",
        "authoritative_completeness": "not_established",
        "execution_authorized": False,
    }


def test_auxiliary_numeric_lexemes_use_decimal_without_float_rounding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "precise.jsonl"
    path.write_text(
        '{"actual_usd":0.12345678901234567890123456789,'
        '"completion_tokens":1,"finish_reason":"stop","probe_id":"p",'
        '"prompt_tokens":1,"reserved_usd":1,"ts":"2026-08-29T01:00:00Z"}\n',
        encoding="utf-8",
        newline="\n",
    )
    record = inventory.build_inventory(
        window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END,
        auxiliary_sources=(("precise", path),),
        project_root=tmp_path,
    )
    assert record["totals"]["actual_spend_usd"] == "0.12345678901234567890123456789"
    assert record["totals"]["uncertain_spend_usd"] == "0"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "ts": "2026-08-30T00:00:00Z"}
            ),
            "outside the half-open billing window",
        ),
        (
            lambda rows: rows[0].update({"reserved_usd": 0.01, "actual_usd": 0.02}),
            "actual_usd exceeds reserved_usd",
        ),
        (
            lambda rows: rows[0].update({"actual_usd": -0.01}),
            "finite and non-negative",
        ),
        (
            lambda rows: rows[0].update({"reserved_usd": "0.04"}),
            "must be a non-negative JSON number",
        ),
        (
            lambda rows: rows[0].update({"actual_usd": "0.03"}),
            "must be a non-negative JSON number",
        ),
        (
            lambda rows: rows[0].update({"unexpected": True}),
            "fields drifted",
        ),
    ],
)
def test_auxiliary_rows_fail_closed(
    tmp_path: Path, mutate: Any, message: str,
) -> None:
    rows = _aux_rows()
    mutate(rows)
    path = _write_aux(tmp_path / "screen.jsonl", rows)
    with pytest.raises(inventory.BillingEvidenceInventoryError, match=message):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("screen", path),),
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows[1].update({"actual_usd": 0.01}),
            "error outcome requires null actual_usd",
        ),
        (
            lambda rows: rows[0].update({"actual_usd": None}),
            "success outcome requires non-null actual_usd",
        ),
        (
            lambda rows: rows[0].update({"probe_id": ""}),
            "non-empty exact string",
        ),
        (
            lambda rows: rows[1].update({"attempt": -1}),
            "non-negative JSON integer lexeme",
        ),
        (
            lambda rows: rows[0].update({"prompt_tokens": 1.5}),
            "non-negative JSON integer lexeme",
        ),
        (
            lambda rows: rows[1].update({"error": ""}),
            "non-empty exact string",
        ),
        (
            lambda rows: rows[0].update({"finish_reason": ""}),
            "non-empty exact string",
        ),
    ],
)
def test_auxiliary_outcome_nullness_and_field_types_are_coupled(
    tmp_path: Path, mutate: Any, message: str,
) -> None:
    rows = _aux_rows()
    mutate(rows)
    path = _write_aux(tmp_path / "screen.jsonl", rows)
    with pytest.raises(inventory.BillingEvidenceInventoryError, match=message):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("screen", path),),
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("row_index", "field", "value"),
    [
        (1, "attempt", 0.0),
        (1, "attempt", "0"),
        (0, "prompt_tokens", 4.0),
        (0, "prompt_tokens", "4"),
        (0, "completion_tokens", 2.0),
        (0, "completion_tokens", "2"),
    ],
)
def test_auxiliary_integer_fields_require_json_integer_lexemes(
    tmp_path: Path, row_index: int, field: str, value: Any,
) -> None:
    rows = _aux_rows()
    rows[row_index][field] = value
    path = _write_aux(tmp_path / "screen.jsonl", rows)
    with pytest.raises(
        inventory.BillingEvidenceInventoryError,
        match="non-negative JSON integer lexeme",
    ):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("screen", path),),
            project_root=tmp_path,
        )


def test_auxiliary_config_and_prompt_key_must_be_nonempty(tmp_path: Path) -> None:
    row = _aux_rows()[0]
    row.pop("probe_id")
    row.update({"config": "", "prompt_key": "prompt"})
    path = _write_aux(tmp_path / "screen.jsonl", [row])
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="non-empty exact string"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("screen", path),),
            project_root=tmp_path,
        )


def test_duplicate_json_key_in_source_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    path.write_text(
        '{"ts":"2026-08-29T01:00:00Z","probe_id":"p",'
        '"reserved_usd":1,"reserved_usd":2,"actual_usd":null,'
        '"attempt":0,"error":"fake"}\n',
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="repeats key"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("duplicate", path),),
            project_root=tmp_path,
        )


def test_duplicate_ids_and_raw_hashes_are_rejected_across_sources(tmp_path: Path) -> None:
    first = _write_aux(tmp_path / "first.jsonl", _aux_rows())
    second = tmp_path / "second.jsonl"
    second.write_bytes(first.read_bytes())

    with pytest.raises(inventory.BillingEvidenceInventoryError, match="source IDs"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("same", first), ("same", second)),
            project_root=tmp_path,
        )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="raw hashes"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("first", first), ("second", second)),
            project_root=tmp_path,
        )


def test_exact_raw_auxiliary_row_cannot_appear_in_distinct_sources(
    tmp_path: Path,
) -> None:
    rows = _aux_rows()
    distinct = deepcopy(rows[1])
    distinct.update({"ts": "2026-08-29T01:02:00Z", "probe_id": "p-distinct"})
    first = _write_aux(tmp_path / "first.jsonl", rows)
    second = _write_aux(tmp_path / "second.jsonl", [rows[0], distinct])
    assert first.read_bytes() != second.read_bytes()

    with pytest.raises(
        inventory.BillingEvidenceInventoryError,
        match="exact raw auxiliary rows must be unique",
    ):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            auxiliary_sources=(("first", first), ("second", second)),
            project_root=tmp_path,
        )


def test_auxiliary_row_digest_does_not_canonicalize_json(tmp_path: Path) -> None:
    row = _aux_rows()[0]
    first = _write_aux(tmp_path / "first.jsonl", [row])
    second = tmp_path / "second.jsonl"
    second.write_text(
        json.dumps(row, sort_keys=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert first.read_bytes() != second.read_bytes()

    record = inventory.build_inventory(
        window_start_utc=WINDOW_START,
        window_end_utc=WINDOW_END,
        auxiliary_sources=(("first", first), ("second", second)),
        project_root=tmp_path,
    )
    assert record["totals"]["actual_spend_usd"] == "0.06"


def test_distinct_snapshots_of_one_immutable_ledger_cannot_be_double_counted(
    tmp_path: Path,
) -> None:
    first = _write_ledger(
        tmp_path / "snapshot-a.jsonl",
        [{"status": "reserved", **_usage_fields(attempt_id="first", cost=0.1)}],
        ledger_id="shared-ledger",
    )
    second = _write_ledger(
        tmp_path / "snapshot-b.jsonl",
        [
            {"status": "reserved", **_usage_fields(attempt_id="second", cost=0.2)},
            {
                "status": "unknown_charge",
                **_usage_fields(attempt_id="second", cost=0.2),
                "error": "fake timeout",
            },
        ],
        ledger_id="shared-ledger",
    )
    assert first.read_bytes() != second.read_bytes()
    assert (
        api_client.usage_ledger_state_path(first).read_bytes()
        != api_client.usage_ledger_state_path(second).read_bytes()
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="ledger IDs"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("snapshot-a", first), ("snapshot-b", second)),
            project_root=tmp_path,
        )


def test_attempt_identity_cannot_appear_in_distinct_immutable_ledgers(
    tmp_path: Path,
) -> None:
    first = _write_ledger(
        tmp_path / "ledger-a.jsonl",
        [{"status": "reserved", **_usage_fields(attempt_id="shared-attempt", cost=0.1)}],
        ledger_id="ledger-a",
    )
    second = _write_ledger(
        tmp_path / "ledger-b.jsonl",
        [{"status": "reserved", **_usage_fields(attempt_id="shared-attempt", cost=0.2)}],
        ledger_id="ledger-b",
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="attempt IDs"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("ledger-a", first), ("ledger-b", second)),
            project_root=tmp_path,
        )


@pytest.mark.parametrize("drifting_artifact", ["ledger", "state"])
def test_ledger_state_pair_drift_across_acquisition_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drifting_artifact: str,
) -> None:
    ledger = _settled_and_open_ledger(tmp_path / "usage.jsonl").resolve()
    state = api_client.usage_ledger_state_path(ledger).resolve()
    target = ledger if drifting_artifact == "ledger" else state
    original_read_bytes = Path.read_bytes
    reads = 0

    def drifting_read_bytes(path: Path) -> bytes:
        nonlocal reads
        raw = original_read_bytes(path)
        if path.resolve() == target:
            reads += 1
            if reads == 3:
                return raw + b"\n"
        return raw

    monkeypatch.setattr(Path, "read_bytes", drifting_read_bytes)
    with pytest.raises(
        inventory.BillingEvidenceInventoryError,
        match="changed across ledger/state snapshot acquisition",
    ):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("ledger", ledger),),
            project_root=tmp_path,
        )
    assert reads == 3


def test_ledger_state_must_already_match_exact_tail(tmp_path: Path) -> None:
    ledger = _settled_and_open_ledger(tmp_path / "usage.jsonl")
    state_path = api_client.usage_ledger_state_path(ledger)
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    state_path.write_text(
        json.dumps({
            "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
            "ledger_id": "fake-ledger",
            "last_sequence": 2,
            "last_event_hash": rows[2]["event_hash"],
        }) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="exact immutable tail"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("ledger", ledger),),
            project_root=tmp_path,
        )


def test_ledger_chain_lifecycle_and_extra_fields_fail_closed(tmp_path: Path) -> None:
    ledger = _settled_and_open_ledger(tmp_path / "usage.jsonl")
    raw = ledger.read_text(encoding="utf-8")
    ledger.write_text(raw.replace('"cost_usd": 0.2', '"cost_usd": 0.21'), encoding="utf-8")
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="chain or lifecycle"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("ledger", ledger),),
            project_root=tmp_path,
        )

    terminal_without_reservation = _write_ledger(
        tmp_path / "lifecycle.jsonl",
        [{
            "status": "success",
            **_usage_fields(attempt_id="missing", cost=0.01),
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "response_metadata": None,
        }],
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="chain or lifecycle"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("lifecycle", terminal_without_reservation),),
            project_root=tmp_path,
        )

    extra = _usage_fields(attempt_id="extra", cost=0.1)
    extra["unexpected"] = True
    ledger = _write_ledger(tmp_path / "extra.jsonl", [{"status": "reserved", **extra}])
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="fields drifted"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("extra", ledger),),
            project_root=tmp_path,
        )


def test_ledger_billing_row_outside_window_is_rejected(tmp_path: Path) -> None:
    ledger = _write_ledger(
        tmp_path / "outside.jsonl",
        [{"status": "reserved", **_usage_fields(attempt_id="outside", cost=0.1)}],
    )
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[1]["ts"] = WINDOW_END
    rows[1]["event_hash"] = api_client._usage_event_hash(rows[1])
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    api_client.usage_ledger_state_path(ledger).write_text(
        json.dumps({
            "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
            "ledger_id": "fake-ledger",
            "last_sequence": 1,
            "last_event_hash": rows[1]["event_hash"],
        }) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="outside the half-open"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            ledger_sources=(("outside", ledger),),
            project_root=tmp_path,
        )


def test_validator_rejects_unsorted_claims_tamper_and_duplicate_record_keys(
    tmp_path: Path,
) -> None:
    record = _build_mixed(tmp_path)
    unsorted = deepcopy(record)
    unsorted["sources"].reverse()
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="sorted by source ID"):
        inventory.validate_inventory(unsorted, project_root=tmp_path)

    tampered = deepcopy(record)
    tampered["sources"][0]["raw_sha256"] = "0" * 64
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="claims do not match"):
        inventory.validate_inventory(tampered, project_root=tmp_path)

    output = tmp_path / "inventory.json"
    duplicate_json = json.dumps(record)[:-1] + ',"stage":"main"}'
    output.write_text(duplicate_json, encoding="utf-8")
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="repeats key"):
        inventory.load_and_validate_inventory(output, project_root=tmp_path)


def test_atomic_publication_fault_leaves_no_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _build_mixed(tmp_path)
    output = tmp_path / "inventory.json"
    link_attempted = False

    def fail_link(source: Path, destination: Path) -> None:
        nonlocal link_attempted
        link_attempted = True
        assert Path(source).read_bytes().endswith(b"\n")
        assert Path(destination) == output
        raise OSError("injected link failure")

    monkeypatch.setattr(inventory.os, "link", fail_link)
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="atomically publish"):
        inventory.write_inventory_exclusive(output, record)
    assert link_attempted is True
    assert not output.exists()
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_publication_reopens_exact_bytes_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _build_mixed(tmp_path)
    output = tmp_path / "inventory.json"
    original_read_bytes = Path.read_bytes

    def corrupt_publication_reopen(path: Path) -> bytes:
        raw = original_read_bytes(path)
        if path.resolve() == output.resolve():
            return raw + b"injected"
        return raw

    monkeypatch.setattr(Path, "read_bytes", corrupt_publication_reopen)
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="bytes differ"):
        inventory.write_inventory_exclusive(output, record)
    complete_raw = original_read_bytes(output)
    assert complete_raw.endswith(b"\n")
    assert not complete_raw.endswith(b"injected")

    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="refusing to overwrite"):
        inventory.write_inventory_exclusive(output, record)
    assert output.read_bytes() == complete_raw


def test_cli_requires_explicit_sources_writes_once_and_reopens(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    aux = _write_aux(tmp_path / "screen.jsonl", _aux_rows())
    output = tmp_path / "inventory.json"
    args = [
        "--project-root", str(tmp_path),
        "--window-start-utc", WINDOW_START,
        "--window-end-utc", WINDOW_END,
        "--aux", f"screen={aux}",
        "--output", str(output),
    ]
    assert cli.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["inventory_path"] == output.as_posix()
    assert summary["authoritative_completeness"] == "not_established"
    assert summary["execution_authorized"] is False
    assert inventory.load_and_validate_inventory(output, project_root=tmp_path)[
        "row_count"
    ] == 2
    original = output.read_bytes()
    with pytest.raises(SystemExit):
        cli.main(args)
    assert output.read_bytes() == original


def test_builder_rejects_empty_explicit_source_list(tmp_path: Path) -> None:
    with pytest.raises(inventory.BillingEvidenceInventoryError, match="at least one"):
        inventory.build_inventory(
            window_start_utc=WINDOW_START,
            window_end_utc=WINDOW_END,
            project_root=tmp_path,
        )
