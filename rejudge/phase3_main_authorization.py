"""Detached-signature verification for the exact Phase 3 main authorization bytes."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rejudge import phase3_owner_signing


OWNER_SIGNATURE_NAMESPACE = "selvarath-phase3-main-authorization-v1"
OWNER_SIGNATURE_PRINCIPAL = "jack-maiorino"
SSH_KEYGEN_PATH = Path("C:/Windows/System32/OpenSSH/ssh-keygen.exe")


class MainAuthorizationSignatureError(ValueError):
    """The exact owner authorization bytes lack a valid pinned-key signature."""


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
    try:
        if source.read_bytes() != raw or signature.read_bytes() != signature_raw:
            raise MainAuthorizationSignatureError(
                "owner authorization or signature changed during verification")
    except OSError as exc:
        raise MainAuthorizationSignatureError(
            "owner authorization became unreadable after signature verification") from exc
    return value


__all__ = [
    "MainAuthorizationSignatureError",
    "OWNER_SIGNATURE_NAMESPACE",
    "OWNER_SIGNATURE_PRINCIPAL",
    "SSH_KEYGEN_PATH",
    "load_authenticated_owner_authorization",
]
