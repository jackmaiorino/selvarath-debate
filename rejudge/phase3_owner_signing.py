"""Single ratification-bound owner public-key configuration for Phase 3.

The capacity and main authorization namespaces are deliberately distinct, but both
must authenticate Jack through the same explicitly selected public key. Keeping the
unratified defaults here makes pinning one reviewed source change and prevents the two
execution surfaces from drifting to different keys.
"""
from __future__ import annotations


OWNER_SIGNING_PUBLIC_KEY: str | None = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGQyxbstc6o6r6mzG4gKJOT/4tb4gmWpD5LcO0rvZNOY "
    "Jack@DESKTOP-DJ1C40R"
)
OWNER_SIGNING_KEY_FINGERPRINT: str | None = (
    "SHA256:e3z7s2CQDDLg2nx/XDI93Tm+JerqZSo8H0GRL88Szmk"
)


__all__ = [
    "OWNER_SIGNING_KEY_FINGERPRINT",
    "OWNER_SIGNING_PUBLIC_KEY",
]
