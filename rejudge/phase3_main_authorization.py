"""Detached-signature verification for the exact Phase 3 main authorization bytes."""
from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rejudge import phase3_owner_signing


OWNER_SIGNATURE_NAMESPACE = "selvarath-phase3-main-authorization-v1"
OWNER_SIGNATURE_PRINCIPAL = "jack-maiorino"
SSH_KEYGEN_PATH = Path("C:/Windows/System32/OpenSSH/ssh-keygen.exe")


class MainAuthorizationSignatureError(ValueError):
    """The exact owner authorization bytes lack a valid pinned-key signature."""


class _SignatureVerificationCache:
    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        self.owner_thread = threading.current_thread()
        self.active = True
        # Values are only success tokens, never parsed authorization objects.
        self.successes: OrderedDict[tuple[Any, ...], None] = OrderedDict()

    def contains(self, key: tuple[Any, ...]) -> bool:
        return key in self.successes

    def remember(self, key: tuple[Any, ...]) -> None:
        self.successes[key] = None
        self.successes.move_to_end(key)
        while len(self.successes) > self.max_entries:
            self.successes.popitem(last=False)


_SIGNATURE_VERIFICATION_CACHE: ContextVar[_SignatureVerificationCache | None] = (
    ContextVar("phase3_owner_signature_verification_cache", default=None)
)


@contextmanager
def cache_owner_signature_verifications(max_entries: int = 128) -> Iterator[None]:
    """Opt in to exact positive signature reuse in this context and thread only.

    Every load still reopens and parses its inputs. Caller-side authorization,
    dispatch-window, policy, and artifact checks are unaffected. No cache persists
    after the scope exits, and nested scopes restore their predecessor on exit.
    """
    if type(max_entries) is not int or max_entries <= 0:
        raise ValueError("signature cache max_entries must be a positive integer")
    cache = _SignatureVerificationCache(max_entries)
    token = _SIGNATURE_VERIFICATION_CACHE.set(cache)
    try:
        yield
    finally:
        cache.active = False
        cache.successes.clear()
        _SIGNATURE_VERIFICATION_CACHE.reset(token)


def _cacheable_plain_ed25519_signature(public_key: str, signature_raw: bytes) -> bool:
    """Only raw Ed25519 signatures have reusable, time-independent validity here.

    OpenSSH certificates and unknown signature/key formats remain uncached. This
    format check never authenticates a signature; the existing verifier does that.
    """
    try:
        key_line = public_key.strip()
        if "\n" in key_line or "\r" in key_line:
            return False
        fields = key_line.split()
        if len(fields) < 2 or fields[0] != "ssh-ed25519":
            return False
        key_blob = base64.b64decode(fields[1], validate=True)
        if (len(key_blob) != 51
                or key_blob[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"):
            return False
        lines = signature_raw.strip().splitlines()
        if (len(lines) < 3 or lines[0] != b"-----BEGIN SSH SIGNATURE-----"
                or lines[-1] != b"-----END SSH SIGNATURE-----"):
            return False
        blob = base64.b64decode(b"".join(lines[1:-1]), validate=True)
        # SSHSIG magic, uint32 version, then the SSH-string public-key blob.
        if len(blob) < 14 or blob[:10] != b"SSHSIG\x00\x00\x00\x01":
            return False
        size = int.from_bytes(blob[10:14], "big")
        return size == len(key_blob) and blob[14:14 + size] == key_blob
    except (ValueError, UnicodeError, TypeError):
        return False


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    # Windows path stat synthesizes executable permission bits from the suffix;
    # fstat does not. The actual file type, identity, bytes and times must agree.
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size,
            value.st_mtime_ns, getattr(value, "st_birthtime_ns", value.st_ctime_ns))


def _verifier_snapshot(verifier: Path) -> tuple[Any, ...]:
    """Reopen the exact executable and bind bytes plus filesystem identity."""
    try:
        resolved = verifier.resolve(strict=True)
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise MainAuthorizationSignatureError("signature verifier is not a regular file")
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(handle.fileno())
        if (_stat_identity(before) != _stat_identity(after)
                or verifier.resolve(strict=True) != resolved
                or _stat_identity(resolved.stat()) != _stat_identity(after)):
            raise MainAuthorizationSignatureError("signature verifier changed while read")
        return (str(verifier), resolved.as_posix(), _stat_identity(after), digest.digest())
    except OSError as exc:
        raise MainAuthorizationSignatureError("signature verifier became unreadable") from exc


def _require_signed_bytes_unchanged(
    source: Path, signature: Path, raw: bytes, signature_raw: bytes,
) -> None:
    try:
        if source.read_bytes() != raw or signature.read_bytes() != signature_raw:
            raise MainAuthorizationSignatureError(
                "owner authorization or signature changed during verification")
    except OSError as exc:
        raise MainAuthorizationSignatureError(
            "owner authorization became unreadable after signature verification") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MainAuthorizationSignatureError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def load_authenticated_owner_authorization(
    path: str | Path,
    *,
    public_key: str | None = None,
    key_fingerprint: str | None = None,
    ssh_keygen_path: str | Path | None = None,
    signature_namespace: str = OWNER_SIGNATURE_NAMESPACE,
) -> dict[str, Any]:
    """Load one strict JSON object and verify its exact bytes with an SSH signature."""
    source = Path(path).resolve()
    if (not isinstance(signature_namespace, str) or not signature_namespace
            or any(not (character.isalnum() or character in "._-")
                   for character in signature_namespace)):
        raise MainAuthorizationSignatureError("signature namespace must be a nonempty token")
    signature = source.with_name(f"{source.name}.sig")
    pinned_key = (
        phase3_owner_signing.OWNER_SIGNING_PUBLIC_KEY
        if public_key is None
        else public_key
    )
    pinned_fingerprint = (
        phase3_owner_signing.OWNER_SIGNING_KEY_FINGERPRINT
        if key_fingerprint is None else key_fingerprint
    )
    verifier = Path(ssh_keygen_path or SSH_KEYGEN_PATH)
    if pinned_key is None or pinned_fingerprint is None:
        raise MainAuthorizationSignatureError(
            "owner signing key is not pinned; live main authorization remains blocked")
    try:
        raw = source.read_bytes()
        signature_raw = signature.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except MainAuthorizationSignatureError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MainAuthorizationSignatureError(
            f"could not load signed main authorization and sidecar: {source}") from exc
    if not isinstance(value, dict):
        raise MainAuthorizationSignatureError("main authorization must be a JSON object")
    if not signature_raw:
        raise MainAuthorizationSignatureError(
            "main authorization detached signature is empty")
    if not verifier.is_file():
        raise MainAuthorizationSignatureError(
            f"pinned ssh-keygen verifier is unavailable: {verifier}")

    cache = _SIGNATURE_VERIFICATION_CACHE.get()
    cache_key = None
    verifier_snapshot = None
    if (cache is not None and cache.active and cache.owner_thread is threading.current_thread()
            and verifier.is_absolute()
            and _cacheable_plain_ed25519_signature(pinned_key, signature_raw)):
        key_bytes = (pinned_key.strip() + "\n").encode("utf-8")
        allowed_bytes = (
            f'{OWNER_SIGNATURE_PRINCIPAL} namespaces="{signature_namespace}" '
            f"{pinned_key.strip()}\n"
        ).encode("utf-8")
        verifier_snapshot = _verifier_snapshot(verifier)
        cache_key = (
            source.as_posix(), signature.as_posix(),
            len(raw), hashlib.sha256(raw).digest(),
            len(signature_raw), hashlib.sha256(signature_raw).digest(),
            key_bytes, pinned_fingerprint, OWNER_SIGNATURE_PRINCIPAL,
            signature_namespace, allowed_bytes, verifier_snapshot,
        )
        if cache.contains(cache_key):
            if _verifier_snapshot(verifier) != verifier_snapshot:
                raise MainAuthorizationSignatureError("signature verifier changed during reuse")
            _require_signed_bytes_unchanged(source, signature, raw, signature_raw)
            cache.remember(cache_key)
            return value

    try:
        with tempfile.TemporaryDirectory(prefix="phase3-main-auth-") as temp_text:
            temp = Path(temp_text)
            public_key_path = temp / "owner.pub"
            allowed = temp / "allowed_signers"
            public_key_path.write_text(
                pinned_key.strip() + "\n", encoding="utf-8", newline="\n")
            allowed.write_text(
                f'{OWNER_SIGNATURE_PRINCIPAL} namespaces="{signature_namespace}" '
                f"{pinned_key.strip()}\n",
                encoding="utf-8",
                newline="\n",
            )
            fingerprint = subprocess.run(
                [str(verifier), "-lf", str(public_key_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            fields = fingerprint.stdout.split()
            observed_fingerprint = fields[1] if len(fields) >= 2 else None
            if fingerprint.returncode != 0 or observed_fingerprint != pinned_fingerprint:
                raise MainAuthorizationSignatureError(
                    "pinned owner public key does not match its required fingerprint")
            # OpenSSH for Windows 9.5p2 never sees end-of-file on a pipe here, so
            # ``ssh-keygen -Y verify`` blocks until the timeout when the message is fed
            # through ``input=``. Hand it a real file handle over the exact bytes that
            # were read above; the post-verification reread below still binds the file.
            message = temp / "message.bin"
            message.write_bytes(raw)
            with message.open("rb") as message_handle:
                verified = subprocess.run(
                    [
                        str(verifier),
                        "-Y", "verify",
                        "-f", str(allowed),
                        "-I", OWNER_SIGNATURE_PRINCIPAL,
                        "-n", signature_namespace,
                        "-s", str(signature),
                    ],
                    stdin=message_handle,
                    capture_output=True,
                    timeout=30,
                )
    except MainAuthorizationSignatureError:
        raise
    except subprocess.TimeoutExpired as exc:
        # On OpenSSH for Windows 9.5p2 a signature that does not verify (tampered
        # message or namespace mismatch) leaves ``ssh-keygen -Y verify`` blocked instead of
        # exiting nonzero. A verifier that never confirms the signature is a failed
        # verification; nothing is accepted without exit 0 and the "Good" line below.
        raise MainAuthorizationSignatureError(
            "owner authorization detached signature is invalid "
            "(verifier did not confirm it before its deadline)") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise MainAuthorizationSignatureError(
            "owner authorization signature verification failed") from exc
    if verified.returncode != 0 or not verified.stdout.startswith(
        f'Good "{signature_namespace}" signature for '
        f"{OWNER_SIGNATURE_PRINCIPAL}".encode("utf-8")
    ):
        raise MainAuthorizationSignatureError(
            "owner authorization detached signature is invalid")
    _require_signed_bytes_unchanged(source, signature, raw, signature_raw)
    if cache_key is not None:
        if _verifier_snapshot(verifier) != verifier_snapshot:
            raise MainAuthorizationSignatureError("signature verifier changed during verification")
        _require_signed_bytes_unchanged(source, signature, raw, signature_raw)
        assert cache is not None
        cache.remember(cache_key)
    return value


__all__ = [
    "MainAuthorizationSignatureError",
    "OWNER_SIGNATURE_NAMESPACE",
    "OWNER_SIGNATURE_PRINCIPAL",
    "SSH_KEYGEN_PATH",
    "cache_owner_signature_verifications",
    "load_authenticated_owner_authorization",
]
