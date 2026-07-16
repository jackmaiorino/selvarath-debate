# Research artifact manifest

`local-research-artifacts.json` records SHA-256 checksums, byte sizes, and JSONL row counts for
the local pilot, Stage-1, mechanism, replay, and calibration artifacts. The payloads remain
intentionally outside Git because they total roughly 573 MB.

The manifest currently labels them `local-only` and has no retrieval URLs. It protects integrity
on this machine, but it is **not a backup**. Before the Phase-2 main run, copy the payloads to a
durable versioned store, verify this manifest there, and fill in `retrieval_uri` values in a newly
generated release manifest.

Build or verify the local manifest with:

```bash
uv run python -m rejudge.artifact_manifest build --root . \
  --out artifacts/local-research-artifacts.json "data/*.jsonl" "rejudge/output/**/*"
uv run python -m rejudge.artifact_manifest verify --root . \
  artifacts/local-research-artifacts.json
```
