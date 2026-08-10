"""One-shot executor for the incident-5 store remediation of 2026-08-09.

Drops the 329 REVIEWER_UNAVAILABLE marker rulings and the 1,070-cell contamination
closure computed by scripts/compute_contamination_closure.py, using the incident-3
rebuild machinery unchanged. See rejudge/phase2_main_incident5_replay_2026-08-09.json.

Must run under a POSIX host (the archive lock uses fcntl), i.e. the run's WSL venv.
"""
import json
import sys

sys.path.insert(0, ".")

from rejudge.phase2_canary_live import (
    rebuild_call_cache,
    rebuild_decision_store_dropping_unreviewed,
    rebuild_result_store,
)

man = json.load(open("rejudge/phase2_main_manifest_2026-08-06c.json", encoding="utf-8"))
closure = json.load(open("analysis_out/contamination_closure.json", encoding="utf-8"))
drop = set(closure["total_drop_set"])
print(f"drop set: {len(drop)} cells ({len(closure['directly_exposed_cells'])} direct, "
      f"{len(closure['dependent_cells'])} dependent)")

r1 = rebuild_decision_store_dropping_unreviewed(
    man, project_root=".", suffix=".reviewer-unavailable-replay-2026-08-09")
print(f"decision store: kept {r1['kept']}, dropped {len(r1['dropped'])}, "
      f"retired to {r1['retired_to']}")

r2 = rebuild_result_store(man, project_root=".", drop_cells=drop)
print(f"result store: kept {r2['kept']}, dropped {len(r2['dropped'])}, "
      f"retired to {r2['retired_to']}")

r3 = rebuild_call_cache(man, project_root=".", drop_cells=drop)
print(f"call cache: kept {r3['kept']}, dropped_rows {r3['dropped_rows']}, "
      f"retired to {r3['retired_to']}")
print("all three rebuilds complete and re-verified on load")
