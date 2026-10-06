"""Read-only account/catalog receipts. Never submits inference or purchases credits."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import httpx


def capture(name: str, url: str, key_name: str, extra: dict | None = None) -> tuple[str, dict]:
    headers = {"Authorization": "Bearer " + os.environ[key_name]}
    headers.update(extra or {})
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
            status, raw, received = response.status_code, response.content, dict(response.headers)
    except Exception as error:
        return name, {"url": url, "method": "GET", "error": type(error).__name__}
    return name, {"url": url, "method": "GET", "status": status,
                  "headers": {k: v for k, v in received.items()
                              if k.lower() in ("date", "openai-project", "openai-organization")
                              or k.lower().startswith("x-ratelimit")},
                  "body": raw.decode("utf-8", errors="replace")}


def main() -> None:
    root = Path(os.environ.get("FINALPHASE_ROOT", "D:/finalphase-runs/final-phase-2026-10-03"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jobs = [
        ("openai_models", "https://api.openai.com/v1/models", "OPENAI_API_KEY"),
        ("openai_batches", "https://api.openai.com/v1/batches?limit=100", "OPENAI_API_KEY"),
        ("openai_credit", "https://api.openai.com/v1/dashboard/billing/credit_grants", "OPENAI_API_KEY"),
        ("openai_projects", "https://api.openai.com/v1/organization/projects?limit=100", "OPENAI_API_KEY"),
        ("together_identity", "https://api.together.ai/v1/whoami", "TOGETHER_API_KEY"),
        ("together_credit", "https://api.together.ai/v1/credits", "TOGETHER_API_KEY"),
        ("runpod_placements", "https://rest.runpod.io/v1/pods", "RUNPOD_API_KEY"),
    ]
    with ThreadPoolExecutor(max_workers=6) as executor:
        receipts = dict(executor.map(lambda job: capture(*job), jobs))
    record = {"checked_utc": stamp, "inference_calls": 0, "purchases": 0,
              "admin_key_configured": bool(os.environ.get("OPENAI_ADMIN_KEY")), "receipts": receipts}
    target = root / f"validation_account_check_{stamp}.json"
    target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    summary = {name: {"status": receipt.get("status"), "error": receipt.get("error")}
               for name, receipt in receipts.items()}
    models = receipts["openai_models"]
    if models.get("status") == 200:
        catalog = {row["id"] for row in json.loads(models["body"]).get("data", [])}
        summary["openai_models"]["astra_available"] = "gpt-6-astra" in catalog
    print(json.dumps({"receipt": str(target), "summary": summary, "inference_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
