"""Single ratification-bound owner public-key configuration for Phase 3.

The capacity and main authorization namespaces are deliberately distinct, but both
must authenticate Jack through the same explicitly selected public key. Keeping the
unratified defaults here makes pinning one reviewed source change and prevents the two
execution surfaces from drifting to different keys.
"""
from __future__ import annotations


OWNER_SIGNING_PUBLIC_KEY: str | None = None
OWNER_SIGNING_KEY_FINGERPRINT: str | None = None


__all__ = [
    "OWNER_SIGNING_KEY_FINGERPRINT",
    "OWNER_SIGNING_PUBLIC_KEY",
]
