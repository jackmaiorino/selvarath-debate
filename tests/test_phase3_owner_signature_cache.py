"""Signature reuse tests use a fake verifier; they do not create SSH signatures."""
from __future__ import annotations

import base64
from contextvars import copy_context
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import threading

import pytest

from rejudge import phase3_main_authorization as auth


def _string(raw: bytes) -> bytes:
    return len(raw).to_bytes(4, "big") + raw


def _unsigned_test_envelope(key_blob: bytes, suffix: bytes = b"fixture-not-a-signature") -> bytes:
    raw = b"SSHSIG\x00\x00\x00\x01" + _string(key_blob) + suffix
    return (b"-----BEGIN SSH SIGNATURE-----\n" + base64.b64encode(raw)
            + b"\n-----END SSH SIGNATURE-----\n")


@pytest.fixture
def fixture_verifier(tmp_path, monkeypatch):
    key_blob = _string(b"ssh-ed25519") + _string(b"x" * 32)
    key = "ssh-ed25519 " + base64.b64encode(key_blob).decode() + " fixture"
    message = b'{"run_id":"run-A","expires":"2000-01-01T00:00:00+00:00","nested":{"x":1}}'
    signature = _unsigned_test_envelope(key_blob)
    source = tmp_path / "authorization.json"
    sidecar = tmp_path / "authorization.json.sig"
    verifier = tmp_path / "verifier.exe"
    source.write_bytes(message)
    sidecar.write_bytes(signature)
    verifier.write_bytes(b"fake-local-verifier")
    state = {
        "source": source, "sidecar": sidecar, "verifier": verifier,
        "message": message, "signature": signature, "key": key, "key_blob": key_blob,
        "calls": [], "timeout": False, "after_verify": None,
    }

    def run(argv, **kwargs):
        state["calls"].append(tuple(argv))
        if argv[1] == "-lf":
            return subprocess.CompletedProcess(argv, 0, "256 SHA256:fixture owner (ED25519)\n", "")
        assert argv[1:3] == ["-Y", "verify"]
        if state["timeout"]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        principal = argv[argv.index("-I") + 1]
        namespace = argv[argv.index("-n") + 1]
        allowed = Path(argv[argv.index("-f") + 1]).read_bytes()
        valid = (
            kwargs["stdin"].read() == message
            and Path(argv[argv.index("-s") + 1]).read_bytes() == signature
            and Path(argv[0]).read_bytes() == b"fake-local-verifier"
            and principal == "jack-maiorino"
            and namespace == "selvarath-phase3-main-authorization-v1"
            and allowed == f'{principal} namespaces="{namespace}" {key}\n'.encode()
        )
        if state["after_verify"]:
            state["after_verify"]()
        return subprocess.CompletedProcess(
            argv, 0 if valid else 1,
            f'Good "{namespace}" signature for {principal}\n'.encode() if valid else b"invalid",
            b"",
        )

    monkeypatch.setattr(auth.subprocess, "run", run)

    def load(**kwargs):
        defaults = dict(public_key=key, key_fingerprint="SHA256:fixture", ssh_keygen_path=verifier)
        defaults.update(kwargs)
        return auth.load_authenticated_owner_authorization(source, **defaults)

    state["load"] = load
    return state


def test_default_is_uncached(fixture_verifier):
    f = fixture_verifier
    assert f["load"]() == f["load"]()
    assert len(f["calls"]) == 4


def test_opt_in_reuses_only_success_and_returns_fresh_objects(fixture_verifier):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        first = f["load"]()
        first["nested"]["x"] = 2
        assert f["load"]()["nested"]["x"] == 1
        assert len(f["calls"]) == 2
    f["load"]()
    assert len(f["calls"]) == 4


@pytest.mark.parametrize("target", ["source", "sidecar", "verifier"])
def test_same_size_same_mtime_tamper_does_not_hit(fixture_verifier, target):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        path = f[target]
        before = path.stat()
        raw = path.read_bytes()
        if target == "source":
            changed = raw.replace(b"run-A", b"run-B")
        elif target == "sidecar":
            changed = _unsigned_test_envelope(f["key_blob"], b"fixture-not-a-signaturF")
        else:
            changed = raw.replace(b"fake", b"FAKE")
        assert len(changed) == len(raw)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(auth.MainAuthorizationSignatureError):
            f["load"]()
        assert len(f["calls"]) == 4


@pytest.mark.parametrize("change", ["namespace", "public_key", "fingerprint", "principal"])
def test_signer_inputs_cannot_reuse_another_success(fixture_verifier, monkeypatch, change):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        kwargs = {}
        if change == "namespace": kwargs["signature_namespace"] = "another-namespace"
        elif change == "public_key": kwargs["public_key"] = f["key"] + " changed"
        elif change == "fingerprint": kwargs["key_fingerprint"] = "SHA256:another"
        else: monkeypatch.setattr(auth, "OWNER_SIGNATURE_PRINCIPAL", "someone-else")
        with pytest.raises(auth.MainAuthorizationSignatureError):
            f["load"](**kwargs)
        assert len(f["calls"]) >= 3


def test_verifier_path_and_file_identity_are_separate_cache_inputs(fixture_verifier, tmp_path):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        alternate = tmp_path / "alternate.exe"
        alternate.write_bytes(f["verifier"].read_bytes())
        f["load"](ssh_keygen_path=alternate)
        assert len(f["calls"]) == 4
        replacement = tmp_path / "replacement.exe"
        replacement.write_bytes(f["verifier"].read_bytes())
        old_identity = f["verifier"].stat().st_ino
        replacement.replace(f["verifier"])
        assert f["verifier"].stat().st_ino != old_identity
        f["load"]()
        assert len(f["calls"]) == 6


@pytest.mark.parametrize("change", ["duplicate_json_key", "missing_signature"])
def test_warm_cache_retains_strict_input_checks(fixture_verifier, change):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        if change == "duplicate_json_key":
            f["source"].write_bytes(b'{"run_id":"run-A","run_id":"run-A"}')
        else:
            f["sidecar"].unlink()
        with pytest.raises(auth.MainAuthorizationSignatureError):
            f["load"]()
        assert len(f["calls"]) == 2


@pytest.mark.parametrize("failure", ["timeout", "invalid"])
def test_failed_verification_is_never_cached(fixture_verifier, failure):
    f = fixture_verifier
    if failure == "timeout": f["timeout"] = True
    else: f["source"].write_bytes(f["message"].replace(b"run-A", b"run-B"))
    with auth.cache_owner_signature_verifications():
        for _ in range(2):
            with pytest.raises(auth.MainAuthorizationSignatureError): f["load"]()
        assert len(f["calls"]) == 4


@pytest.mark.parametrize("target", ["source", "sidecar", "verifier"])
def test_cold_verification_must_finish_with_same_bytes(fixture_verifier, target):
    f = fixture_verifier
    raw = f[target].read_bytes()
    f["after_verify"] = lambda: f[target].write_bytes(raw + b" ")
    with auth.cache_owner_signature_verifications():
        with pytest.raises(auth.MainAuthorizationSignatureError): f["load"]()
        f[target].write_bytes(raw)
        f["after_verify"] = None
        f["load"]()
        assert len(f["calls"]) == 4


def test_warm_reuse_rereads_signed_bytes(fixture_verifier, monkeypatch):
    f = fixture_verifier
    snapshot = auth._verifier_snapshot
    count = 0

    def mutate_during_reuse(path):
        nonlocal count
        value = snapshot(path)
        count += 1
        if count == 2:
            f["source"].write_bytes(f["message"].replace(b"run-A", b"run-B"))
        return value

    with auth.cache_owner_signature_verifications():
        f["load"]()
        monkeypatch.setattr(auth, "_verifier_snapshot", mutate_during_reuse)
        with pytest.raises(auth.MainAuthorizationSignatureError): f["load"]()
        assert len(f["calls"]) == 2


def test_context_exception_reset_and_nested_restore(fixture_verifier):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        with pytest.raises(RuntimeError):
            with auth.cache_owner_signature_verifications():
                f["load"]()
                raise RuntimeError("fixture")
        f["load"]()
        assert len(f["calls"]) == 4
    f["load"]()
    assert len(f["calls"]) == 6


def test_context_does_not_share_success_with_another_thread(fixture_verifier):
    f = fixture_verifier
    failures = []
    with auth.cache_owner_signature_verifications():
        f["load"]()
        copied = copy_context()

        def run():
            try: copied.run(f["load"])
            except BaseException as exc: failures.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert failures == []
        assert len(f["calls"]) == 4
        f["load"]()
        assert len(f["calls"]) == 4


def test_copied_context_cannot_reuse_cache_after_owning_scope_exits(fixture_verifier):
    f = fixture_verifier
    with auth.cache_owner_signature_verifications():
        f["load"]()
        copied = copy_context()
    copied.run(f["load"])
    assert len(f["calls"]) == 4


def test_lru_bound_reverifies_evicted_authorizations(fixture_verifier, tmp_path):
    f = fixture_verifier
    second = tmp_path / "second.json"
    second.write_bytes(f["message"])
    second.with_suffix(".json.sig").write_bytes(f["signature"])
    with auth.cache_owner_signature_verifications(max_entries=1):
        f["load"]()
        auth.load_authenticated_owner_authorization(
            second, public_key=f["key"], key_fingerprint="SHA256:fixture",
            ssh_keygen_path=f["verifier"],
        )
        assert len(auth._SIGNATURE_VERIFICATION_CACHE.get().successes) == 1
        f["load"]()
        assert len(f["calls"]) == 6


def test_signature_reuse_does_not_grant_a_dispatch_window(fixture_verifier):
    f = fixture_verifier

    def caller():
        value = f["load"]()
        if datetime.fromisoformat(value["expires"]) < datetime.now(timezone.utc):
            raise ValueError("expired caller window")

    with auth.cache_owner_signature_verifications():
        for _ in range(2):
            with pytest.raises(ValueError, match="expired caller window"): caller()
        assert len(f["calls"]) == 2


def test_certificates_and_unknown_formats_are_not_cacheable(fixture_verifier):
    f = fixture_verifier
    assert auth._cacheable_plain_ed25519_signature(f["key"], f["signature"])
    cert_blob = _string(b"ssh-ed25519-cert-v01@openssh.com") + _string(b"x" * 32)
    assert not auth._cacheable_plain_ed25519_signature(f["key"], _unsigned_test_envelope(cert_blob))
    assert not auth._cacheable_plain_ed25519_signature("ssh-ed25519-cert-v01@openssh.com AAAA", f["signature"])
    assert not auth._cacheable_plain_ed25519_signature(f["key"], b"unknown signature")


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_cache_capacity_must_be_positive_integer(limit):
    with pytest.raises(ValueError):
        with auth.cache_owner_signature_verifications(limit): pass
