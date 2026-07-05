# Limited-Verification Re-analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From the recovered pilot data alone, determine whether the 70B "few-oracle-calls-hurts" dip survives paired/clustered inference and whether it is a fixable oracle artifact (FM1) or a deeper debate failure (FM2), producing a go/no-go for paid follow-ups.

**Architecture:** A standalone `analysis/` Python package (7 focused modules) that loads `data/*.jsonl`, computes descriptive + pre-registered inferential statistics with a question-cluster bootstrap, extracts the small flip-error set for in-session mechanism labeling, and assembles a findings report. Pure local compute — no network, no paid API.

**Tech Stack:** Python 3.14, `uv`, `pandas`, `numpy`, `pytest`. (No `scipy`/`matplotlib` — Wilson CIs and the bootstrap are hand-rolled; outputs are markdown tables.)

## Global Constraints

- Runtime: Python 3.14 via `uv` (repo is already `uv`-managed). Run everything with `uv run ...` from the repo root `selvarath-debate/`.
- **$0 / no API:** no code in `analysis/` may import an LLM SDK or make any network call. FM1/FM2 labeling is a manual in-session step, not code.
- Data: `data/judgments.jsonl` (2583 rows), `data/transcripts.jsonl` (318 rows), `world_specs/{world}.txt`. Already present locally.
- Judge id → short name: `meta-llama/Llama-3.3-70B-Instruct-Turbo` → `70B`; `meta-llama/Meta-Llama-3-8B-Instruct-Lite` → `8B`.
- Primary population: `70B` judge, budgets `[0, 1, 2, 5]`. Budget 20 exploratory only. 8B secondary.
- Pre-registered contrasts (percentage points): `Δfew = ½·[p(1)+p(2)] − p(0)`, `Δrecover5 = ½·[p(1)+p(2)] − p(5)`, where `p(b)` = judge-wrong rate at budget `b`.
- Gate: bank the harm claim iff `Δfew > 0`, cluster-bootstrap 95% CI excludes 0 with lower bound ≳ +2 pp, positive in both correct-side strata, surviving all parse-sensitivity treatments.
- Known hand-checked value (regression anchor): 70B `Δfew` point = `½(8.8+9.4) − 1.9 = 7.2 pp`; counts are `p0=6/318, p1=28/318, p2=30/318, p5=17/318`.
- Commit after every task with `-c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com"` (the clone has no committer identity set).

---

## File Structure

- `analysis/__init__.py` — package marker.
- `analysis/load.py` — read + join judgments/transcripts → tidy `DataFrame`; read world docs.
- `analysis/describe.py` — smoke tables: win rate (+Wilson CI), side split, confidence×correctness.
- `analysis/inference.py` — count matrices, pre-registered contrasts, question-cluster bootstrap CIs.
- `analysis/parse_sensitivity.py` — suspected-fallback flag + `Δfew` under 4 treatments.
- `analysis/mechanism.py` — extract 70B flip-error cases, render for labeling, summarize labels.
- `analysis/robustness.py` — leave-one-world-out + discordance.
- `analysis/run_report.py` — integration: assemble `analysis/output/report.md` and `analysis/output/mechanism_cases.md`.
- `conftest.py` (repo root) — ensures the package is importable under pytest.
- `tests/test_load.py`, `tests/test_describe.py`, `tests/test_inference.py`, `tests/test_parse_sensitivity.py`, `tests/test_mechanism.py`, `tests/test_robustness.py`, `tests/test_report.py`.

---

### Task 1: Package scaffold + data loader

**Files:**
- Create: `analysis/__init__.py`, `analysis/load.py`, `conftest.py`, `tests/test_load.py`
- Modify: `pyproject.toml` (add deps)

**Interfaces:**
- Produces:
  - `load.load_judgments_df(judg_path=None, trans_path=None) -> pandas.DataFrame` with columns: `question_id:str, transcript_index:int, world:str, judge_model:str, judge_short:str, query_budget:int, position_a_is_correct:bool, correct_side:str('A'|'B'), verdict:str, verdict_correct:bool, wrong:bool, queries_used:int, confidence:int, reasoning:str, queries_submitted:list, question:str, correct_answer:str, wrong_answer:str, honest_first:bool, debate_transcript:list`.
  - `load.load_transcripts(path=None) -> dict[(str,int), dict]`
  - `load.load_world(world:str, world_dir=None) -> str`
  - `load.DATA_DIR`, `load.WORLD_DIR` (Path constants).

- [ ] **Step 1: Add dependencies**

Run:
```bash
cd "C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate"
uv add pandas numpy
uv add --dev pytest
```
Expected: `pyproject.toml` gains `pandas`, `numpy`, and a dev `pytest`; `uv.lock` updates.

- [ ] **Step 2: Create the repo-root conftest so `analysis` imports under pytest**

Create `conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
```

- [ ] **Step 3: Write the failing test**

Create `analysis/__init__.py` (empty), then `tests/test_load.py`:
```python
import json
from analysis import load


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _fixture(tmp_path):
    trans = [{
        "question_id": "Q1", "transcript_index": 0, "world": "selvarath",
        "question": "q?", "correct_answer": "C", "wrong_answer": "W",
        "honest_first": True, "debate_transcript": [{"speaker": "honest", "text": "hi"}],
    }]
    judg = [{
        "question_id": "Q1", "transcript_index": 0,
        "judge_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "query_budget": 0, "position_a_is_correct": True,
        "queries_submitted": [], "queries_used": 0, "verdict": "Position A",
        "verdict_correct": True, "confidence": 4, "reasoning": "because", "seed": 1,
    }]
    tp = tmp_path / "transcripts.jsonl"; jp = tmp_path / "judgments.jsonl"
    _write_jsonl(tp, trans); _write_jsonl(jp, judg)
    return jp, tp


def test_join_and_derived_columns(tmp_path):
    jp, tp = _fixture(tmp_path)
    df = load.load_judgments_df(jp, tp)
    assert len(df) == 1
    row = df.iloc[0]
    assert row.world == "selvarath"          # joined from transcript
    assert row.judge_short == "70B"
    assert row.correct_side == "A"           # position_a_is_correct True
    assert row.wrong is False or row.wrong == False  # ~verdict_correct
    assert row.question == "q?"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_load.py -v`
Expected: FAIL (`AttributeError: module 'analysis.load' has no attribute 'load_judgments_df'`).

- [ ] **Step 5: Implement `analysis/load.py`**

```python
"""Load and join the pilot judgment + transcript data into one tidy table."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO / "data"
WORLD_DIR = _REPO / "world_specs"

JUDGE_SHORT = {
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": "70B",
    "meta-llama/Meta-Llama-3-8B-Instruct-Lite": "8B",
}


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_transcripts(path=None):
    path = Path(path) if path else DATA_DIR / "transcripts.jsonl"
    return {(r["question_id"], r["transcript_index"]): r for r in _read_jsonl(path)}


def load_judgments_df(judg_path=None, trans_path=None):
    judg_path = Path(judg_path) if judg_path else DATA_DIR / "judgments.jsonl"
    transcripts = load_transcripts(trans_path)
    rows = []
    for j in _read_jsonl(judg_path):
        t = transcripts.get((j["question_id"], j["transcript_index"]), {})
        rows.append({
            "question_id": j["question_id"],
            "transcript_index": j["transcript_index"],
            "world": t.get("world"),
            "judge_model": j["judge_model"],
            "judge_short": JUDGE_SHORT.get(j["judge_model"], j["judge_model"]),
            "query_budget": j["query_budget"],
            "position_a_is_correct": j["position_a_is_correct"],
            "correct_side": "A" if j["position_a_is_correct"] else "B",
            "verdict": j["verdict"],
            "verdict_correct": bool(j["verdict_correct"]),
            "wrong": not bool(j["verdict_correct"]),
            "queries_used": j["queries_used"],
            "confidence": j["confidence"],
            "reasoning": j.get("reasoning", "") or "",
            "queries_submitted": j.get("queries_submitted", []),
            "question": t.get("question"),
            "correct_answer": t.get("correct_answer"),
            "wrong_answer": t.get("wrong_answer"),
            "honest_first": t.get("honest_first"),
            "debate_transcript": t.get("debate_transcript", []),
        })
    return pd.DataFrame(rows)


def load_world(world, world_dir=None):
    world_dir = Path(world_dir) if world_dir else WORLD_DIR
    return (world_dir / f"{world}.txt").read_text(encoding="utf-8")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_load.py -v`
Expected: PASS.

- [ ] **Step 7: Add a real-data sanity test**

Append to `tests/test_load.py`:
```python
import pytest
from analysis.load import DATA_DIR


real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_real_data_counts():
    df = load.load_judgments_df()
    assert len(df) == 2583
    assert df.world.notna().all()            # every judgment joined a transcript
    assert set(df.judge_short) == {"70B", "8B"}
    assert df[df.judge_short == "70B"].world.nunique() == 3
```

- [ ] **Step 8: Run and commit**

Run: `uv run pytest tests/test_load.py -v` → Expected: PASS (2 passed).
```bash
git add analysis/__init__.py analysis/load.py conftest.py tests/test_load.py pyproject.toml uv.lock
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: data loader + join"
```

---

### Task 2: Descriptive tables

**Files:**
- Create: `analysis/describe.py`, `tests/test_describe.py`

**Interfaces:**
- Consumes: `load.load_judgments_df` DataFrame.
- Produces:
  - `describe.wilson_ci(k:int, n:int, z=1.96) -> (lo_pct:float, hi_pct:float)`
  - `describe.win_rate_table(df, judge:str) -> DataFrame[judge,budget,n,wrong_pct,ci_lo,ci_hi]`
  - `describe.side_stratified_table(df, judge) -> DataFrame[judge,budget,wrong_pct_Acorrect,n_A,wrong_pct_Bcorrect,n_B]`
  - `describe.confidence_by_correctness(df, judge) -> DataFrame[judge,budget,mean_conf,mean_conf_correct,mean_conf_wrong]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_describe.py`:
```python
import pytest
from analysis import describe, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def test_wilson_ci_known():
    lo, hi = describe.wilson_ci(6, 318)
    assert 0.8 < lo < 1.1 and 3.9 < hi < 4.3     # ~[0.9, 4.1]


@real
def test_70b_budget0_winrate():
    df = load.load_judgments_df()
    tbl = describe.win_rate_table(df, "70B").set_index("budget")
    assert abs(tbl.loc[0, "wrong_pct"] - 1.887) < 0.05   # 6/318
    assert tbl.loc[0, "n"] == 318


@real
def test_side_split_matches_known():
    df = load.load_judgments_df()
    tbl = describe.side_stratified_table(df, "70B").set_index("budget")
    assert abs(tbl.loc[2, "wrong_pct_Acorrect"] - 9.9) < 0.6
    assert abs(tbl.loc[2, "wrong_pct_Bcorrect"] - 9.0) < 0.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_describe.py -v`
Expected: FAIL (`module 'analysis.describe' has no attribute 'wilson_ci'`).

- [ ] **Step 3: Implement `analysis/describe.py`**

```python
"""Descriptive smoke tables — NOT primary inference."""
from __future__ import annotations

import math

import pandas as pd


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (100 * (center - half), 100 * (center + half))


def win_rate_table(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        cell = sub[sub.query_budget == b]
        n = len(cell); k = int(cell.wrong.sum())
        lo, hi = wilson_ci(k, n)
        out.append({"judge": judge, "budget": b, "n": n,
                    "wrong_pct": 100 * k / n if n else float("nan"),
                    "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out)


def side_stratified_table(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        row = {"judge": judge, "budget": b}
        for side in ("A", "B"):
            cell = sub[(sub.query_budget == b) & (sub.correct_side == side)]
            n = len(cell); k = int(cell.wrong.sum())
            row[f"wrong_pct_{side}correct"] = (100 * k / n) if n else float("nan")
            row[f"n_{side}"] = n
        out.append(row)
    return pd.DataFrame(out)


def confidence_by_correctness(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        cell = sub[sub.query_budget == b]
        correct = cell[~cell.wrong]; wrong = cell[cell.wrong]
        out.append({"judge": judge, "budget": b,
                    "mean_conf": cell.confidence.mean(),
                    "mean_conf_correct": correct.confidence.mean() if len(correct) else float("nan"),
                    "mean_conf_wrong": wrong.confidence.mean() if len(wrong) else float("nan")})
    return pd.DataFrame(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_describe.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analysis/describe.py tests/test_describe.py
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: descriptive tables"
```

---

### Task 3: Primary inference (contrasts + cluster bootstrap)

**Files:**
- Create: `analysis/inference.py`, `tests/test_inference.py`

**Interfaces:**
- Consumes: `load.load_judgments_df` DataFrame.
- Produces:
  - `inference.PRIMARY_BUDGETS = [0, 1, 2, 5]`
  - `inference._count_matrices(df, judge, budgets, correct_side=None) -> (qids:np.ndarray, W:np.ndarray[Q,B], N:np.ndarray[Q,B])`
  - `inference.point_estimate(df, judge, stat='few'|'recover5', budgets=PRIMARY_BUDGETS, correct_side=None) -> float  # pp`
  - `inference.cluster_bootstrap_ci(df, judge, stat, budgets, correct_side, B=10000, seed=0, alpha=0.05) -> (lo_pp, hi_pp)`
  - `inference.summarize(df, judge, budgets=PRIMARY_BUDGETS, B=10000, seed=0) -> DataFrame[stat,stratum,point_pp,ci_lo_pp,ci_hi_pp]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_inference.py`:
```python
import numpy as np
import pytest
from analysis import inference, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_delta_few_point_is_7_2():
    df = load.load_judgments_df()
    assert abs(inference.point_estimate(df, "70B", "few") - 7.2) < 0.15


@real
def test_bootstrap_ci_brackets_point_and_excludes_zero():
    df = load.load_judgments_df()
    pt = inference.point_estimate(df, "70B", "few")
    lo, hi = inference.cluster_bootstrap_ci(df, "70B", "few", B=2000, seed=0)
    assert lo < pt < hi
    assert lo > 0                     # harm effect excludes zero


@real
def test_bootstrap_is_seeded():
    df = load.load_judgments_df()
    a = inference.cluster_bootstrap_ci(df, "70B", "few", B=1000, seed=7)
    b = inference.cluster_bootstrap_ci(df, "70B", "few", B=1000, seed=7)
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_inference.py -v`
Expected: FAIL (`module 'analysis.inference' has no attribute 'point_estimate'`).

- [ ] **Step 3: Implement `analysis/inference.py`**

```python
"""Primary inference: pre-registered contrasts with question-cluster bootstrap CIs.

Δfew      = 1/2 [p(1) + p(2)] - p(0)
Δrecover5 = 1/2 [p(1) + p(2)] - p(5)
p(b) = judge-wrong rate at oracle budget b. Values reported in percentage points.
"""
from __future__ import annotations

import numpy as np

PRIMARY_BUDGETS = [0, 1, 2, 5]


def _count_matrices(df, judge, budgets, correct_side=None):
    sub = df[df.judge_short == judge]
    if correct_side is not None:
        sub = sub[sub.correct_side == correct_side]
    qids = sorted(sub.question_id.unique())
    qindex = {q: i for i, q in enumerate(qids)}
    bindex = {b: j for j, b in enumerate(budgets)}
    W = np.zeros((len(qids), len(budgets)))
    N = np.zeros((len(qids), len(budgets)))
    for r in sub.itertuples(index=False):
        if r.query_budget not in bindex:
            continue
        i, j = qindex[r.question_id], bindex[r.query_budget]
        N[i, j] += 1
        if r.wrong:
            W[i, j] += 1
    return np.array(qids), W, N


def _p_from_sums(Wsum, Nsum):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(Nsum > 0, Wsum / Nsum, np.nan)


def _stat(p, budgets, stat):
    b = {v: i for i, v in enumerate(budgets)}
    few = 0.5 * (p[b[1]] + p[b[2]]) - p[b[0]]
    if stat == "few":
        return few
    if stat == "recover5":
        return 0.5 * (p[b[1]] + p[b[2]]) - p[b[5]]
    raise ValueError(stat)


def point_estimate(df, judge, stat="few", budgets=PRIMARY_BUDGETS, correct_side=None):
    _, W, N = _count_matrices(df, judge, budgets, correct_side)
    p = _p_from_sums(W.sum(0), N.sum(0))
    return float(_stat(p, budgets, stat)) * 100


def cluster_bootstrap_ci(df, judge, stat="few", budgets=PRIMARY_BUDGETS,
                         correct_side=None, B=10000, seed=0, alpha=0.05):
    _, W, N = _count_matrices(df, judge, budgets, correct_side)
    Q = W.shape[0]
    rng = np.random.default_rng(seed)
    vals = np.empty(B)
    for it in range(B):
        idx = rng.integers(0, Q, Q)
        p = _p_from_sums(W[idx].sum(0), N[idx].sum(0))
        vals[it] = _stat(p, budgets, stat)
    lo = float(np.nanpercentile(vals, 100 * alpha / 2)) * 100
    hi = float(np.nanpercentile(vals, 100 * (1 - alpha / 2))) * 100
    return lo, hi


def summarize(df, judge, budgets=PRIMARY_BUDGETS, B=10000, seed=0):
    import pandas as pd
    rows = []
    for stat in ("few", "recover5"):
        for side in (None, "A", "B"):
            pt = point_estimate(df, judge, stat, budgets, side)
            lo, hi = cluster_bootstrap_ci(df, judge, stat, budgets, side, B, seed)
            rows.append({"stat": stat, "stratum": side or "overall",
                         "point_pp": pt, "ci_lo_pp": lo, "ci_hi_pp": hi})
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_inference.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add analysis/inference.py tests/test_inference.py
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: pre-registered contrasts + cluster bootstrap"
```

---

### Task 4: Parse-sensitivity bound

**Files:**
- Create: `analysis/parse_sensitivity.py`, `tests/test_parse_sensitivity.py`

**Interfaces:**
- Consumes: DataFrame; `inference._count_matrices`, `inference._p_from_sums`, `inference._stat`, `inference.PRIMARY_BUDGETS`.
- Produces:
  - `parse_sensitivity.suspected_fallback(row) -> bool`
  - `parse_sensitivity.flag(df) -> DataFrame` (adds `suspect:bool`)
  - `parse_sensitivity.delta_few_under_treatments(df, judge, budgets=PRIMARY_BUDGETS) -> dict[str,float]` with keys `baseline, exclude, suspect_wrong, suspect_correct, suspect_5050` (all pp).

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_sensitivity.py`:
```python
import pytest
from analysis import parse_sensitivity as ps, load
from analysis.load import DATA_DIR
from types import SimpleNamespace

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def test_detector_fires_on_leaked_format():
    leaked = SimpleNamespace(reasoning="VERDICT: Position B\nCONFIDENCE: 1\nblah")
    clean = SimpleNamespace(reasoning="The honest debater cited exact figures.")
    assert ps.suspected_fallback(leaked) is True
    assert ps.suspected_fallback(clean) is False


@real
def test_treatments_return_five_and_survive():
    df = load.load_judgments_df()
    t = ps.delta_few_under_treatments(df, "70B")
    assert set(t) == {"baseline", "exclude", "suspect_wrong",
                      "suspect_correct", "suspect_5050"}
    # The effect should not collapse to ~0 under any treatment.
    assert min(t.values()) > 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_parse_sensitivity.py -v`
Expected: FAIL (`module 'analysis.parse_sensitivity' has no attribute 'suspected_fallback'`).

- [ ] **Step 3: Implement `analysis/parse_sensitivity.py`**

```python
"""Bounded parse-fallback sensitivity for Δfew.

The pilot never logged raw verdict text, so a definitive parse audit is impossible
here (that is deliverable C). We approximate: the clean parser writes only the
REASONING: line into `reasoning`; leaked VERDICT:/CONFIDENCE: tokens signal the
model broke format and thus had a higher chance of hitting the silent
default-to-Position-B fallback. We recompute Δfew treating those rows four ways.
"""
from __future__ import annotations

import numpy as np

from analysis.inference import (PRIMARY_BUDGETS, _count_matrices, _p_from_sums, _stat)


def suspected_fallback(row):
    r = (getattr(row, "reasoning", "") or "").upper()
    return ("VERDICT:" in r) or ("CONFIDENCE:" in r)


def flag(df):
    df = df.copy()
    df["suspect"] = [suspected_fallback(r) for r in df.itertuples(index=False)]
    return df


def _delta_few_pp(df, judge, budgets):
    _, W, N = _count_matrices(df, judge, budgets)
    p = _p_from_sums(W.sum(0), N.sum(0))
    return float(_stat(p, budgets, "few")) * 100


def delta_few_under_treatments(df, judge, budgets=PRIMARY_BUDGETS):
    df = flag(df)
    wcol = df.columns.get_loc("wrong")
    treatments = {"baseline": _delta_few_pp(df, judge, budgets),
                  "exclude": _delta_few_pp(df[~df.suspect], judge, budgets)}

    d = df.copy(); d.loc[d.suspect, "wrong"] = True
    treatments["suspect_wrong"] = _delta_few_pp(d, judge, budgets)

    d = df.copy(); d.loc[d.suspect, "wrong"] = False
    treatments["suspect_correct"] = _delta_few_pp(d, judge, budgets)

    d = df.copy()
    idx = np.where(d.suspect.values)[0]
    coin = np.random.default_rng(0).random(len(idx)) < 0.5
    d.iloc[idx[coin], wcol] = True
    d.iloc[idx[~coin], wcol] = False
    treatments["suspect_5050"] = _delta_few_pp(d, judge, budgets)
    return treatments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_parse_sensitivity.py -v`
Expected: PASS. (If `test_treatments_return_five_and_survive` fails because a treatment drops ≤ 2.0, that is a **finding**, not a test bug — stop and report it per the gate.)

- [ ] **Step 5: Commit**

```bash
git add analysis/parse_sensitivity.py tests/test_parse_sensitivity.py
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: bounded parse-sensitivity"
```

---

### Task 5: Mechanism extraction + label summary

**Files:**
- Create: `analysis/mechanism.py`, `tests/test_mechanism.py`

**Interfaces:**
- Consumes: DataFrame; `load.load_world`.
- Produces:
  - `mechanism.extract_flip_cases(df, judge='70B', base_budget=0, flip_budgets=(1,2)) -> list[dict]` — each dict: `question_id, transcript_index, world, flip_budget, question, correct_answer, wrong_answer, oracle_exchanges, reasoning, debate_transcript`.
  - `mechanism.render_cases_markdown(cases, world_dir=None) -> str` — human-readable, includes the world doc per case.
  - `mechanism.summarize_labels(labels) -> DataFrame[label,count,frac]` where `labels` is a list of dicts (or DataFrame) each with a `label` in {`FM1`,`FM2`,`other`}.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mechanism.py`:
```python
import pandas as pd
from analysis import mechanism


def _row(qid, ti, budget, correct, side="A"):
    return {"question_id": qid, "transcript_index": ti, "judge_short": "70B",
            "query_budget": budget, "verdict_correct": correct, "wrong": not correct,
            "correct_side": side, "world": "selvarath", "question": "q",
            "correct_answer": "C", "wrong_answer": "W",
            "queries_submitted": [{"query": "x", "response": "NO"}],
            "reasoning": "r", "debate_transcript": []}


def test_extract_finds_correct0_wrong2_flip():
    df = pd.DataFrame([
        _row("Q1", 0, 0, True),    # correct at budget 0
        _row("Q1", 0, 2, False),   # wrong at budget 2  -> a flip
        _row("Q2", 0, 0, True),
        _row("Q2", 0, 2, True),    # stays correct -> not a flip
    ])
    cases = mechanism.extract_flip_cases(df)
    assert len(cases) == 1
    assert cases[0]["question_id"] == "Q1" and cases[0]["flip_budget"] == 2


def test_summarize_labels_counts_fractions():
    out = mechanism.summarize_labels([{"label": "FM1"}, {"label": "FM2"}, {"label": "FM1"}])
    d = out.set_index("label")
    assert d.loc["FM1", "count"] == 2
    assert abs(d.loc["FM1", "frac"] - 2 / 3) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mechanism.py -v`
Expected: FAIL (`module 'analysis.mechanism' has no attribute 'extract_flip_cases'`).

- [ ] **Step 3: Implement `analysis/mechanism.py`**

```python
"""Extract the 70B correct@0 -> wrong@{1,2} flip cases for in-session FM1/FM2 labeling.

No API calls: this only prepares human-readable cases and tallies labels supplied
by the analyst.
"""
from __future__ import annotations

import pandas as pd

from analysis.load import load_world


def extract_flip_cases(df, judge="70B", base_budget=0, flip_budgets=(1, 2)):
    sub = df[df.judge_short == judge]
    base = (sub[sub.query_budget == base_budget]
            .set_index(["question_id", "transcript_index"])["verdict_correct"])
    cases = []
    for fb in flip_budgets:
        for r in sub[sub.query_budget == fb].itertuples(index=False):
            key = (r.question_id, r.transcript_index)
            if key in base.index and bool(base.loc[key]) and r.wrong:
                cases.append({
                    "question_id": r.question_id,
                    "transcript_index": r.transcript_index,
                    "world": r.world,
                    "flip_budget": fb,
                    "question": r.question,
                    "correct_answer": r.correct_answer,
                    "wrong_answer": r.wrong_answer,
                    "oracle_exchanges": r.queries_submitted,
                    "reasoning": r.reasoning,
                    "debate_transcript": r.debate_transcript,
                })
    return cases


def render_cases_markdown(cases, world_dir=None):
    worlds = {}
    parts = [f"# FM1/FM2 labeling — {len(cases)} flip cases (70B, correct@0 -> wrong@{{1,2}})\n"]
    for i, c in enumerate(cases, 1):
        if c["world"] not in worlds:
            worlds[c["world"]] = load_world(c["world"], world_dir)
        parts.append(f"\n---\n\n## Case {i}: {c['question_id']} / t{c['transcript_index']} "
                     f"(flip budget {c['flip_budget']})\n")
        parts.append(f"**Question:** {c['question']}\n")
        parts.append(f"**Correct answer:** {c['correct_answer']}\n")
        parts.append(f"**Wrong answer:** {c['wrong_answer']}\n")
        parts.append("\n**Oracle exchanges:**\n")
        for ex in c["oracle_exchanges"]:
            parts.append(f"- Q: {ex.get('query')} -> **{ex.get('response')}**\n")
        parts.append(f"\n**Judge reasoning:** {c['reasoning']}\n")
        parts.append("\n**Debate transcript:**\n")
        for turn in c["debate_transcript"]:
            parts.append(f"- ({turn.get('speaker')}) {turn.get('text')}\n")
        parts.append(f"\n<details><summary>World doc: {c['world']}</summary>\n\n"
                     f"{worlds[c['world']]}\n\n</details>\n")
        parts.append(f"\n**LABEL (FM1 / FM2 / other):** _____\n")
    return "".join(parts)


def summarize_labels(labels):
    df = pd.DataFrame(list(labels))
    total = len(df)
    out = (df.groupby("label").size().rename("count").reset_index())
    out["frac"] = out["count"] / total if total else 0.0
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mechanism.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analysis/mechanism.py tests/test_mechanism.py
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: flip-case extraction + label summary"
```

---

### Task 6: Robustness (leave-one-world-out + discordance)

**Files:**
- Create: `analysis/robustness.py`, `tests/test_robustness.py`

**Interfaces:**
- Consumes: DataFrame; `inference.point_estimate`, `inference.PRIMARY_BUDGETS`.
- Produces:
  - `robustness.leave_one_world_out(df, judge, budgets=PRIMARY_BUDGETS) -> DataFrame[dropped, delta_few_pp]` (first row `dropped="none"`).
  - `robustness.discordance(df, judge, base_budget=0, flip_budgets=(1,2)) -> DataFrame[flip_budget, correct_to_wrong, wrong_to_correct, net_new_errors, n_transcripts]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_robustness.py`:
```python
import pandas as pd
import pytest
from analysis import robustness, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def _row(qid, ti, budget, correct):
    return {"question_id": qid, "transcript_index": ti, "judge_short": "70B",
            "query_budget": budget, "verdict_correct": correct, "wrong": not correct,
            "correct_side": "A", "world": "selvarath"}


def test_discordance_counts_flips():
    df = pd.DataFrame([
        _row("Q1", 0, 0, True), _row("Q1", 0, 1, False),   # correct->wrong
        _row("Q2", 0, 0, False), _row("Q2", 0, 1, True),   # wrong->correct
    ])
    d = robustness.discordance(df, "70B", flip_budgets=(1,)).iloc[0]
    assert d.correct_to_wrong == 1 and d.wrong_to_correct == 1
    assert d.net_new_errors == 0


@real
def test_lowo_has_none_plus_three_worlds():
    df = load.load_judgments_df()
    tbl = robustness.leave_one_world_out(df, "70B")
    assert list(tbl.dropped)[0] == "none"
    assert len(tbl) == 4          # none + 3 worlds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_robustness.py -v`
Expected: FAIL (`module 'analysis.robustness' has no attribute 'discordance'`).

- [ ] **Step 3: Implement `analysis/robustness.py`**

```python
"""Robustness checks: leave-one-world-out on Δfew and per-transcript discordance."""
from __future__ import annotations

import pandas as pd

from analysis.inference import PRIMARY_BUDGETS, point_estimate


def leave_one_world_out(df, judge, budgets=PRIMARY_BUDGETS):
    out = [{"dropped": "none", "delta_few_pp": point_estimate(df, judge, "few", budgets)}]
    for w in sorted(x for x in df.world.dropna().unique()):
        out.append({"dropped": w,
                    "delta_few_pp": point_estimate(df[df.world != w], judge, "few", budgets)})
    return pd.DataFrame(out)


def discordance(df, judge, base_budget=0, flip_budgets=(1, 2)):
    sub = df[df.judge_short == judge]
    base = (sub[sub.query_budget == base_budget]
            .set_index(["question_id", "transcript_index"])["verdict_correct"])
    out = []
    for fb in flip_budgets:
        cur = (sub[sub.query_budget == fb]
               .set_index(["question_id", "transcript_index"])["verdict_correct"])
        j = pd.DataFrame({"base": base, "flip": cur}).dropna()
        c2w = int((j.base & ~j.flip).sum())
        w2c = int((~j.base & j.flip).sum())
        out.append({"flip_budget": fb, "correct_to_wrong": c2w, "wrong_to_correct": w2c,
                    "net_new_errors": c2w - w2c, "n_transcripts": len(j)})
    return pd.DataFrame(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_robustness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analysis/robustness.py tests/test_robustness.py
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: robustness (LOWO + discordance)"
```

---

### Task 7: Report assembly

**Files:**
- Create: `analysis/run_report.py`, `tests/test_report.py`
- Create at runtime: `analysis/output/report.md`, `analysis/output/mechanism_cases.md`

**Interfaces:**
- Consumes: all prior modules.
- Produces:
  - `run_report.build_report(df, B=10000, seed=0, labels=None) -> str` (markdown).
  - `run_report.main(out_dir='analysis/output')` — writes `report.md` + `mechanism_cases.md`; reads `analysis/output/labels.csv` if present to include the FM1/FM2 summary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:
```python
import pytest
from analysis import run_report, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_report_has_key_sections():
    df = load.load_judgments_df()
    md = run_report.build_report(df, B=500, seed=0)
    for header in ["# Limited-Verification Re-analysis", "## Primary inference (70B)",
                   "## Parse-sensitivity", "## Robustness", "## Gate evaluation"]:
        assert header in md
    assert "Δfew" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL (`module 'analysis.run_report' has no attribute 'build_report'`).

- [ ] **Step 3: Implement `analysis/run_report.py`**

```python
"""Assemble the findings report and the mechanism-labeling worksheet."""
from __future__ import annotations

from pathlib import Path

from analysis import describe, inference, mechanism, parse_sensitivity, robustness
from analysis.load import load_judgments_df


def _md_table(df):
    return df.to_markdown(index=False)


def _gate(summary):
    row = summary[(summary.stat == "few") & (summary.stratum == "overall")].iloc[0]
    a = summary[(summary.stat == "few") & (summary.stratum == "A")].iloc[0]
    b = summary[(summary.stat == "few") & (summary.stratum == "B")].iloc[0]
    passed = (row.point_pp > 0 and row.ci_lo_pp > 0 and row.ci_lo_pp >= 2.0
              and a.point_pp > 0 and b.point_pp > 0)
    return passed, row, a, b


def build_report(df, B=10000, seed=0, labels=None):
    summ = inference.summarize(df, "70B", B=B, seed=seed)
    treat = parse_sensitivity.delta_few_under_treatments(df, "70B")
    passed, row, a, b = _gate(summ)
    parse_ok = min(treat.values()) > 2.0

    parts = ["# Limited-Verification Re-analysis\n"]
    parts.append("\n## Win rates (70B)\n\n" + _md_table(describe.win_rate_table(df, "70B")))
    parts.append("\n\n## Side split (70B)\n\n" + _md_table(describe.side_stratified_table(df, "70B")))
    parts.append("\n\n## Confidence x correctness (70B)\n\n"
                 + _md_table(describe.confidence_by_correctness(df, "70B")))
    parts.append("\n\n## Primary inference (70B)\n\nPre-registered contrasts (pp), "
                 "question-cluster bootstrap 95% CI:\n\n" + _md_table(summ))
    parts.append(f"\n\n- Δfew overall = {row.point_pp:.2f} pp "
                 f"[{row.ci_lo_pp:.2f}, {row.ci_hi_pp:.2f}]\n")
    parts.append("\n## Parse-sensitivity (Δfew under treatments, pp)\n\n"
                 + "\n".join(f"- {k}: {v:.2f}" for k, v in treat.items()))
    parts.append("\n\n## Robustness\n\n### Leave-one-world-out (Δfew pp)\n\n"
                 + _md_table(robustness.leave_one_world_out(df, "70B")))
    parts.append("\n\n### Discordance (0 -> {1,2})\n\n"
                 + _md_table(robustness.discordance(df, "70B")))
    parts.append("\n\n## Secondary: 8B (side-bias caveat)\n\n"
                 + _md_table(describe.side_stratified_table(df, "8B")))
    if labels is not None:
        parts.append("\n\n## Mechanism (FM1/FM2)\n\n"
                     + _md_table(mechanism.summarize_labels(labels)))
    parts.append("\n\n## Gate evaluation\n\n"
                 f"- Δfew CI excludes 0 with lower bound ≳ +2pp: **{row.ci_lo_pp:.2f} pp** → "
                 f"{'PASS' if row.ci_lo_pp >= 2.0 else 'FAIL'}\n"
                 f"- Positive in both strata (A={a.point_pp:.2f}, B={b.point_pp:.2f}): "
                 f"{'PASS' if a.point_pp > 0 and b.point_pp > 0 else 'FAIL'}\n"
                 f"- Survives parse-sensitivity (min treatment {min(treat.values()):.2f} pp): "
                 f"{'PASS' if parse_ok else 'FAIL'}\n"
                 f"- **Overall harm claim: {'BANKED' if passed and parse_ok else 'NOT banked'}**\n"
                 "- Next step gated on FM1/FM2 split (see mechanism_cases.md).\n")
    return "".join(parts)


def main(out_dir="analysis/output"):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    df = load_judgments_df()
    labels = None
    labels_csv = out / "labels.csv"
    if labels_csv.exists():
        import csv
        with open(labels_csv, encoding="utf-8") as f:
            labels = list(csv.DictReader(f))
    (out / "report.md").write_text(build_report(df, labels=labels), encoding="utf-8")
    cases = mechanism.extract_flip_cases(df)
    (out / "mechanism_cases.md").write_text(mechanism.render_cases_markdown(cases), encoding="utf-8")
    print(f"Wrote {out/'report.md'} and {out/'mechanism_cases.md'} ({len(cases)} flip cases)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS. (`to_markdown` needs `tabulate`; if it errors, run `uv add tabulate` and re-run.)

- [ ] **Step 5: Generate the artifacts**

Run: `uv run python -m analysis.run_report`
Expected: prints the two output paths and the flip-case count; `analysis/output/report.md` and `analysis/output/mechanism_cases.md` exist.

- [ ] **Step 6: Full test sweep + commit**

Run: `uv run pytest -q`
Expected: all tests pass.
```bash
git add analysis/run_report.py tests/test_report.py analysis/output/report.md
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: report assembly + generated report"
```

---

### Task 8: In-session FM1/FM2 labeling (manual, $0)

**Files:**
- Create: `analysis/output/labels.csv` (columns: `question_id,transcript_index,flip_budget,label` where label ∈ `FM1|FM2|other`)

**This task is not code.** The implementer (assistant) opens `analysis/output/mechanism_cases.md`, reads each flip case against its world doc + oracle exchanges + judge reasoning, and applies the criteria from the spec (§7):
- **FM1** — the judge queried a claim that is actually **true** per the world doc, but the oracle answered **NO/NOT ADDRESSED**, and the reasoning shows the honest side was discounted.
- **FM2** — the oracle answered **YES** to a **true-but-irrelevant** dishonest claim and the reasoning shows over-updating toward it.
- **other** — neither.

- [ ] **Step 1:** Read `analysis/output/mechanism_cases.md` case-by-case; write one row per case into `analysis/output/labels.csv` with a one-line justification kept alongside (in a scratch note).
- [ ] **Step 2:** Re-run `uv run python -m analysis.run_report` so the report includes the FM1/FM2 summary table.
- [ ] **Step 3:** Commit.
```bash
git add analysis/output/labels.csv analysis/output/report.md
git -c user.name="Jack Maiorino" -c user.email="jack.maiorino@gmail.com" commit -m "analysis: FM1/FM2 labels + updated report"
```

---

## Final deliverable

`analysis/output/report.md` — the findings report with the banked/not-banked harm claim, the FM1/FM2 split, robustness, the 8B secondary note, and the confidence-calibration note — plus a one-paragraph **recommendation** on whether to proceed to deliverable C (Llama re-judge + oracle ablation) and/or B (frontier rerun), each still gated on explicit approval + a dollar estimate.

## Self-Review

- **Spec coverage:** loader (§4/unit1→T1); descriptives incl. confidence×correctness (unit2→T2); primary inference + contrasts + cluster bootstrap + strata (unit3, §6→T3); parse-sensitivity bound (unit4→T4); FM1/FM2 mechanism (unit5, §7→T5+T8); robustness LOWO+discordance (unit6→T6); report + gate (unit7, §6→T7); cost $0 / no API (Global Constraints, T8 manual). Budget-20 excluded from PRIMARY_BUDGETS and treated exploratory. 8B secondary via `side_stratified_table(df,"8B")` in report.
- **Placeholder scan:** none — all steps carry runnable code/commands. Task 8 is intentionally a manual analytic step (the $0 constraint) with explicit criteria, not a code placeholder.
- **Type consistency:** `_count_matrices`/`_p_from_sums`/`_stat` names used identically across `inference`, `parse_sensitivity`; `point_estimate`/`PRIMARY_BUDGETS` reused in `robustness`; `extract_flip_cases` dict keys match `render_cases_markdown` usage; `summarize`/`summarize_labels` outputs match `run_report` consumers.
