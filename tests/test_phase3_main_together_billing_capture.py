"""Tests for read-only authenticated Together billing capture."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from decimal import Overflow, Subnormal, localcontext
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_together_billing_capture as capture


API_KEY = "test-secret-never-persist"
API_KEY_ID = "key-test"
PROJECT_ID = "project-test"
ORGANIZATION_ID = "organization-test"
WHOAMI_DATE = "Sat, 29 Aug 2026 21:00:00 GMT"
BILLING_DATE = "Sat, 29 Aug 2026 21:00:01 GMT"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def _whoami() -> dict[str, Any]:
    return {
        "api_key_id": API_KEY_ID,
        "project_id": PROJECT_ID,
        "project_name": "Test Project",
        "project_slug": "test-project",
        "organization_id": ORGANIZATION_ID,
        "organization_name": "Test Organization",
    }


def _line_item(cost: str = "0.10") -> dict[str, Any]:
    return {
        "product_name": "Serverless Inference",
        "quantity": "100",
        "unit_price": "0.001",
        "cost": cost,
        "pricing_dimensions": {"unit": "tokens"},
        "attributes": {
            "api_key_id": API_KEY_ID,
            "project_id": PROJECT_ID,
        },
    }


def _report(*, line_item: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "object": "list",
        "organization_id": ORGANIZATION_ID,
        "billing_period": "2026-08",
        "earliest_window_start": "2026-08-29T19:00:00Z",
        "latest_window_end": "2026-08-29T20:00:00Z",
        "currency": "USD",
        "data": [{
            "date": "2026-08-29",
            "start_time": "2026-08-29T19:00:00Z",
            "end_time": "2026-08-29T20:00:00Z",
            "line_items": [line_item or _line_item()],
        }],
        "next_cursor": None,
    }


class FakeTransport:
    def __init__(
        self,
        *,
        whoami_status: int = 200,
        billing_status: int = 200,
        whoami_body: bytes | None = None,
        billing_body: bytes | None = None,
        billing_date: str = BILLING_DATE,
    ) -> None:
        self.whoami_status = whoami_status
        self.billing_status = billing_status
        self.whoami_body = whoami_body or _json_bytes(_whoami())
        self.billing_body = billing_body or _json_bytes(_report())
        self.billing_date = billing_date
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(
        self, url: str, headers: Any, timeout_seconds: float,
    ) -> capture.HttpGetResponse:
        copied_headers = dict(headers)
        self.calls.append((url, copied_headers, timeout_seconds))
        if url == capture.WHOAMI_URL:
            return capture.HttpGetResponse(
                status=self.whoami_status,
                headers={"Date": WHOAMI_DATE, "Content-Type": "application/json"},
                body=self.whoami_body,
            )
        assert url.startswith(capture.BILLING_USAGE_URL + "?")
        return capture.HttpGetResponse(
            status=self.billing_status,
            headers={"Date": self.billing_date, "Content-Type": "application/json"},
            body=self.billing_body,
        )


def _account() -> str:
    return capture.account_identity_sha256(
        api_key_id=API_KEY_ID,
        project_id=PROJECT_ID,
        organization_id=ORGANIZATION_ID,
    )


def _build(
    tmp_path: Path,
    *,
    transport: FakeTransport | None = None,
) -> tuple[dict[str, Any], dict[Path, bytes], FakeTransport, Path]:
    fake = transport or FakeTransport()
    output = tmp_path / "capture.json"
    record, materials = capture.build_capture(
        output_path=output,
        window_start_utc="2026-08-29T18:00:00Z",
        window_end_utc="2026-08-29T21:00:00Z",
        finalized_through_utc="2026-08-29T20:00:00Z",
        expected_account_identity_sha256=_account(),
        api_key=API_KEY,
        transport=fake,
    )
    return record, materials, fake, output


def test_build_uses_only_fixed_authenticated_gets_and_never_persists_secret(
    tmp_path: Path,
) -> None:
    record, materials, fake, _output = _build(tmp_path)

    assert len(fake.calls) == 2
    assert fake.calls[0][0] == capture.WHOAMI_URL
    assert fake.calls[1][0].startswith(
        capture.BILLING_USAGE_URL
        + "?month=2026-08&organization_id=organization-test&granularity=hour&limit=1000"
    )
    assert all(call[1]["Authorization"] == f"Bearer {API_KEY}" for call in fake.calls)
    assert record["dashboard"]["reported_delta_usd"] == "0.1"
    assert record["settlement"] == {
        "status": capture.SETTLEMENT_STATUS,
        "basis": capture.SETTLEMENT_BASIS,
        "finalized_through_utc": "2026-08-29T20:00:00Z",
    }
    assert record["identity"]["account_identity_sha256"] == _account()
    all_bytes = _json_bytes(record) + b"".join(materials.values())
    assert API_KEY.encode("utf-8") not in all_bytes
    assert record["execution_authorized"] is False
    assert record["provider_calls_authorized"] is False
    assert record["main_run_spend_authorized"] is False


@pytest.mark.parametrize(
    ("costs", "expected"),
    [
        (("1000", "1"), "1001"),
        (("0.0001", "0.0002"), "0.0003"),
    ],
)
def test_provider_cost_sum_ignores_ambient_decimal_context(
    tmp_path: Path, costs: tuple[str, str], expected: str,
) -> None:
    report = _report()
    report["data"][0]["line_items"] = [
        _line_item(costs[0]),
        _line_item(costs[1]),
    ]
    with localcontext() as context:
        context.prec = 3
        context.Emax = 2
        context.Emin = -2
        context.traps[Overflow] = True
        context.traps[Subnormal] = True
        record, _materials, _fake, _output = _build(
            tmp_path,
            transport=FakeTransport(billing_body=_json_bytes(report)),
        )

    assert record["dashboard"]["reported_delta_usd"] == expected


def test_capture_cli_runs_directly_by_file_path() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / (
        "phase3_main_capture_together_billing.py")

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "cannot authorize inference, execution, or spend" in result.stdout


def test_publish_is_create_only_and_reopens_exact_response_bodies(tmp_path: Path) -> None:
    record, materials, _fake, output = _build(tmp_path)

    validated = capture.write_capture_exclusive(output, record, materials)

    assert validated["capture_path"] == output.resolve()
    assert validated["provider_delta_usd"] == "0.1"
    assert validated["provider_settlement"]["account_identity_sha256"] == _account()
    assert validated["capture_raw_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    for path, raw in materials.items():
        assert path.read_bytes() == raw
    with pytest.raises(capture.TogetherBillingCaptureError, match="overwrite"):
        capture.write_capture_exclusive(output, record, materials)


def test_disabled_beta_endpoint_publishes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "capture.json"
    fake = FakeTransport(billing_status=404, billing_body=b'{"message":"not found"}\n')

    with pytest.raises(
        capture.TogetherBillingApiUnavailableError,
        match="not enabled",
    ):
        capture.capture_and_write(
            output_path=output,
            window_start_utc="2026-08-29T18:00:00Z",
            window_end_utc="2026-08-29T21:00:00Z",
            finalized_through_utc="2026-08-29T20:00:00Z",
            expected_account_identity_sha256=_account(),
            api_key=API_KEY,
            transport=fake,
        )

    assert not output.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("whoami_status", "billing_status"),
    [(302, 200), (401, 200), (200, 403), (200, 429), (200, 500)],
)
def test_all_other_http_failures_publish_nothing(
    tmp_path: Path, whoami_status: int, billing_status: int,
) -> None:
    with pytest.raises(capture.TogetherBillingCaptureError, match="returned HTTP"):
        capture.capture_and_write(
            output_path=tmp_path / "capture.json",
            window_start_utc="2026-08-29T18:00:00Z",
            window_end_utc="2026-08-29T21:00:00Z",
            finalized_through_utc="2026-08-29T20:00:00Z",
            expected_account_identity_sha256=_account(),
            api_key=API_KEY,
            transport=FakeTransport(
                whoami_status=whoami_status,
                billing_status=billing_status,
            ),
        )
    assert not list(tmp_path.iterdir())


def test_default_transport_rejects_duplicate_headers_and_wraps_http_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status = 200

        def __init__(self, *, fail_read: bool) -> None:
            self.fail_read = fail_read

        def read(self, _limit: int) -> bytes:
            if self.fail_read:
                raise capture.http.client.IncompleteRead(b"", 1)
            return _json_bytes(_whoami())

        def getheaders(self) -> list[tuple[str, str]]:
            return [
                ("Date", WHOAMI_DATE),
                ("Date", BILLING_DATE),
                ("Content-Type", "application/json"),
            ]

    class FakeConnection:
        def __init__(self, *, fail_read: bool) -> None:
            self.response = FakeResponse(fail_read=fail_read)
            self.closed = False

        def request(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def getresponse(self) -> FakeResponse:
            return self.response

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection(fail_read=False)
    monkeypatch.setattr(
        capture.http.client,
        "HTTPSConnection",
        lambda *_args, **_kwargs: connection,
    )
    with pytest.raises(capture.TogetherBillingCaptureError, match="repeats header"):
        capture._default_transport(capture.WHOAMI_URL, {}, 30.0)
    assert connection.closed

    connection = FakeConnection(fail_read=True)
    monkeypatch.setattr(
        capture.http.client,
        "HTTPSConnection",
        lambda *_args, **_kwargs: connection,
    )
    with pytest.raises(capture.TogetherBillingCaptureError, match="request failed"):
        capture._default_transport(capture.WHOAMI_URL, {}, 30.0)
    assert connection.closed


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda report: report.__setitem__("next_cursor", "cursor"), "pagination"),
        (
            lambda report: report.__setitem__("organization_id", "other-org"),
            "identity",
        ),
        (
            lambda report: report["data"][0]["line_items"][0]["attributes"].pop(
                "project_id"),
            "attribution",
        ),
        (
            lambda report: report["data"][0]["line_items"][0].__setitem__(
                "cost", "1e-1"),
            "fixed-point",
        ),
        (
            lambda report: report["data"][0].__setitem__(
                "end_time", "2026-08-29T19:30:00Z"),
            "whole UTC hour",
        ),
    ],
)
def test_report_schema_and_attribution_fail_closed(
    tmp_path: Path, mutator: Any, message: str,
) -> None:
    report = _report()
    mutator(report)
    fake = FakeTransport(billing_body=_json_bytes(report))

    with pytest.raises(capture.TogetherBillingCaptureError, match=message):
        _build(tmp_path, transport=fake)


def test_duplicate_json_key_and_documented_lag_fail_closed(tmp_path: Path) -> None:
    duplicate = _json_bytes(_whoami()).replace(
        b'{', b'{"api_key_id":"duplicate",', 1)
    with pytest.raises(capture.TogetherBillingCaptureError, match="repeats key"):
        _build(tmp_path / "duplicate", transport=FakeTransport(whoami_body=duplicate))

    with pytest.raises(capture.TogetherBillingCaptureError, match="lag elapsed"):
        _build(
            tmp_path / "lag",
            transport=FakeTransport(
                billing_date="Sat, 29 Aug 2026 21:00:00 GMT"),
        )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_number_fails_closed(
    tmp_path: Path, constant: str,
) -> None:
    report = _json_bytes(_report()).replace(
        b'"pricing_dimensions": {"unit": "tokens"}',
        f'"pricing_dimensions": {{"forbidden": {constant}}}'.encode("ascii"),
    )

    with pytest.raises(capture.TogetherBillingCaptureError, match="non-finite"):
        _build(tmp_path, transport=FakeTransport(billing_body=report))


def test_escaped_api_key_in_decoded_response_fails_closed(tmp_path: Path) -> None:
    escaped_key = "".join(f"\\u{ord(character):04x}" for character in API_KEY)
    whoami_raw = _json_bytes(_whoami()).replace(
        b'"Test Organization"', f'"{escaped_key}"'.encode("ascii"))
    assert API_KEY.encode("utf-8") not in whoami_raw

    with pytest.raises(capture.TogetherBillingCaptureError, match="API-key secret"):
        _build(tmp_path, transport=FakeTransport(whoami_body=whoami_raw))


def test_publication_confines_exact_sidecars_to_capture_root(tmp_path: Path) -> None:
    capture_root = tmp_path / "capture-root"
    record, materials, _fake, output = _build(capture_root)
    original = Path(record["whoami"]["response_path"])
    escaped = tmp_path / "escaped-response.json"
    escaped_materials = dict(materials)
    escaped_materials[escaped] = escaped_materials.pop(original)
    record["whoami"]["response_path"] = escaped.as_posix()

    with pytest.raises(capture.TogetherBillingCaptureError, match="capture root"):
        capture.write_capture_exclusive(output, record, escaped_materials)
    assert not output.exists()
    assert not escaped.exists()

    record, materials, _fake, output = _build(capture_root)
    original = Path(record["whoami"]["response_path"])
    arbitrary = output.with_name("arbitrary-response.json")
    arbitrary_materials = dict(materials)
    arbitrary_materials[arbitrary] = arbitrary_materials.pop(original)
    record["whoami"]["response_path"] = arbitrary.as_posix()
    with pytest.raises(capture.TogetherBillingCaptureError, match="derived sidecar set"):
        capture.write_capture_exclusive(output, record, arbitrary_materials)
    assert not arbitrary.exists()


def test_publication_failure_rolls_back_sidecars_created_by_this_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record, materials, _fake, output = _build(tmp_path)
    publish = capture._publish_bytes_exclusive
    calls = 0

    def fail_second(
        path: Path,
        raw: bytes,
        *,
        publication_log: list[tuple[Path, bytes]] | None = None,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise capture.TogetherBillingCaptureError("simulated publication failure")
        publish(path, raw, publication_log=publication_log)

    monkeypatch.setattr(capture, "_publish_bytes_exclusive", fail_second)
    with pytest.raises(capture.TogetherBillingCaptureError, match="simulated"):
        capture.write_capture_exclusive(output, record, materials)

    assert not output.exists()
    assert all(not path.exists() for path in materials)


@pytest.mark.parametrize(
    ("failure_call", "failure"),
    [(1, KeyboardInterrupt()), (3, SystemExit("simulated index interruption"))],
)
def test_publish_then_interrupt_rolls_back_sidecars_and_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
    failure: BaseException,
) -> None:
    record, materials, _fake, output = _build(tmp_path)
    publish = capture._publish_bytes_exclusive
    calls = 0

    def fail_after_publish(
        path: Path,
        raw: bytes,
        *,
        publication_log: list[tuple[Path, bytes]] | None = None,
    ) -> None:
        nonlocal calls
        calls += 1
        publish(path, raw, publication_log=publication_log)
        if calls == failure_call:
            raise failure

    monkeypatch.setattr(capture, "_publish_bytes_exclusive", fail_after_publish)
    with pytest.raises(type(failure)):
        capture.write_capture_exclusive(output, record, materials)

    assert not output.exists()
    assert all(not path.exists() for path in materials)


def test_index_and_raw_body_hash_drift_are_rejected(tmp_path: Path) -> None:
    record, materials, _fake, output = _build(tmp_path)
    capture.write_capture_exclusive(output, record, materials)
    sidecar = next(path for path in materials if "billing-usage" in path.name)
    sidecar.write_bytes(sidecar.read_bytes() + b"\n")

    with pytest.raises(capture.TogetherBillingCaptureError, match="SHA-256 drifted"):
        capture.load_and_validate_capture(output, project_root=tmp_path)

    fresh_root = tmp_path / "index"
    fresh_root.mkdir()
    record, materials, _fake, output = _build(fresh_root)
    capture.write_capture_exclusive(output, record, materials)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["identity"]["account_identity_sha256"] = "0" * 64
    output.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(capture.TogetherBillingCaptureError, match="differs from authenticated"):
        capture.load_and_validate_capture(output, project_root=fresh_root)


def test_cross_month_capture_requires_each_month_and_prior_month_lag(tmp_path: Path) -> None:
    july_report = {
        "object": "list",
        "organization_id": ORGANIZATION_ID,
        "billing_period": "2026-07",
        "earliest_window_start": None,
        "latest_window_end": None,
        "currency": "USD",
        "data": [],
        "next_cursor": None,
    }
    august_report = _report()
    calls: list[str] = []

    def transport(url: str, headers: Any, timeout: float) -> capture.HttpGetResponse:
        del headers, timeout
        calls.append(url)
        if url == capture.WHOAMI_URL:
            return capture.HttpGetResponse(
                200,
                {"Date": WHOAMI_DATE, "Content-Type": "application/json"},
                _json_bytes(_whoami()),
            )
        month = "2026-07" if "month=2026-07" in url else "2026-08"
        report = july_report if month == "2026-07" else august_report
        return capture.HttpGetResponse(
            200,
            {"Date": BILLING_DATE, "Content-Type": "application/json"},
            _json_bytes(report),
        )

    output = tmp_path / "cross-month.json"
    record, materials = capture.build_capture(
        output_path=output,
        window_start_utc="2026-07-31T23:00:00Z",
        window_end_utc="2026-08-29T21:00:00Z",
        finalized_through_utc="2026-08-29T20:00:00Z",
        expected_account_identity_sha256=_account(),
        api_key=API_KEY,
        transport=transport,
    )

    assert [item["query"]["month"] for item in record["billing_usage"]] == [
        "2026-07", "2026-08"
    ]
    assert len(calls) == 3
    capture.write_capture_exclusive(output, record, materials)
    omitted = deepcopy(record)
    omitted["billing_usage"] = omitted["billing_usage"][1:]
    with pytest.raises(capture.TogetherBillingCaptureError, match="every intersecting"):
        capture.validate_capture(omitted, project_root=tmp_path)


def test_zero_usage_month_still_derives_finality_from_provider_date(
    tmp_path: Path,
) -> None:
    report = _report()
    report["earliest_window_start"] = None
    report["latest_window_end"] = None
    report["data"] = []

    record, _materials, _fake, _output = _build(
        tmp_path,
        transport=FakeTransport(billing_body=_json_bytes(report)),
    )

    assert record["dashboard"]["reported_delta_usd"] == "0"
    assert record["settlement"]["finalized_through_utc"] == "2026-08-29T20:00:00Z"


def test_prior_month_lag_boundary_is_strict(tmp_path: Path) -> None:
    july_report = {
        "object": "list",
        "organization_id": ORGANIZATION_ID,
        "billing_period": "2026-07",
        "earliest_window_start": None,
        "latest_window_end": None,
        "currency": "USD",
        "data": [],
        "next_cursor": None,
    }

    def transport(url: str, headers: Any, timeout: float) -> capture.HttpGetResponse:
        del headers, timeout
        if url == capture.WHOAMI_URL:
            return capture.HttpGetResponse(
                200,
                {"Date": WHOAMI_DATE, "Content-Type": "application/json"},
                _json_bytes(_whoami()),
            )
        assert "month=2026-07" in url
        return capture.HttpGetResponse(
            200,
            {
                "Date": "Sun, 02 Aug 2026 00:00:00 GMT",
                "Content-Type": "application/json",
            },
            _json_bytes(july_report),
        )

    with pytest.raises(capture.TogetherBillingCaptureError, match="lag elapsed"):
        capture.build_capture(
            output_path=tmp_path / "capture.json",
            window_start_utc="2026-07-31T23:00:00Z",
            window_end_utc="2026-08-01T01:00:00Z",
            finalized_through_utc="2026-08-01T00:00:00Z",
            expected_account_identity_sha256=_account(),
            api_key=API_KEY,
            transport=transport,
        )
    assert not list(tmp_path.iterdir())
