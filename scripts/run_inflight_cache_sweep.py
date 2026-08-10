"""Second cache sweep of the incident-5 remediation, 2026-08-10.

Cells that were in flight (paused, no result row) during the store surgery kept cached
provider calls made under since-superseded gate rulings. The cache's CallReplayMismatch
guard correctly refuses to replay them against the corrected world, halting the run one
cell at a time. This drops the cached calls of every gate-capable in-flight cell (kinds
debate_judgment and no_debate_judgment) so each re-derives cleanly; pipe-delimited keys
from the older calibration namespace that shares this cache file are left untouched.

Must run under a POSIX host (the archive lock uses fcntl), i.e. the run's WSL venv.
"""
import json
import sys

sys.path.insert(0, ".")

from rejudge.phase2_canary_live import rebuild_call_cache

GATE_KINDS = {"debate_judgment", "no_debate_judgment"}

man = json.load(open("rejudge/phase2_main_manifest_2026-08-06c.json", encoding="utf-8"))
inflight = json.load(open("analysis_out/inflight_cache_cells_2026-08-10.json", encoding="utf-8"))
drop = {c for c in inflight if c.count(":") >= 2 and c.split(":")[1] in GATE_KINDS}
print(f"sweeping cached calls of {len(drop)} gate-capable in-flight cells")

r = rebuild_call_cache(man, project_root=".", drop_cells=drop,
                       suffix=".inflight-replay-2026-08-10")
print(f"call cache: kept {r['kept']}, dropped_rows {r['dropped_rows']}, "
      f"retired to {r['retired_to']}")
print("sweep complete and re-verified on load")
