import os

_DRY_RUN_RESPONSE = "VERDICT: Position A\nCONFIDENCE: 1\nREASONING: Dry run — no API call made."


class LegacyLiveRunDisabledError(RuntimeError):
    """The original pilot harness is retained for reproduction, not new paid runs."""


async def complete(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    seed: int | None = None,
    max_tokens: int = 1024,
) -> str:
    if os.environ.get("DRY_RUN"):
        return _DRY_RUN_RESPONSE

    raise LegacyLiveRunDisabledError(
        "live calls through the original pilot harness are disabled because it has no "
        "manifest-bound cumulative spend ledger and contains known data-corrupting bugs; "
        "use a hardened rejudge runner instead")
