# Fixed-Harness Re-Judge Runner + Report Hygiene — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the half-applied corrections in the published reports, and build a run-ready (but not run) re-judge harness that re-judges the 318 pilot transcripts under configurable arms (clean / bug-replay / placebo / single-bug / legacy) with full raw logging, dual verdict parsing, and hard cost controls.

**Architecture:** A new `rejudge/` package that never modifies the pilot files (`judge.py`, `api.py` stay as the historical record) but *ports* pilot behavior where replay fidelity requires it. Arms are configuration (`ArmSpec`), not code paths: one judge loop with per-arm hooks for oracle normalization, query composition, DONE detection, and placebo injection. Every raw model output is persisted; every verdict is parsed both strict and pilot-compat.

**Tech Stack:** Python 3.13 (uv), pytest, `together` SDK (sync client, injected/stubbed in tests), pandas only in analysis (not in `rejudge/`).

## Global Constraints

- **NO live API calls anywhere in this plan.** All tests run offline with stub clients. `RejudgeClient` is constructed in tests with `_sdk_client=` stubs. Never set a real `TOGETHER_API_KEY`; never call `client.complete` without a stub unless `dry_run=True`.
- **Pilot files are read-only:** `judge.py`, `api.py`, `debate.py`, `orchestrate.py`, `models.py`, `experiment_protocol.json` must not be modified.
- Commit messages must not mention Claude or AI assistance.
- Run tests with `uv run pytest <file> -q` from the repo root (`C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate`).
- Placebo feedback text, verbatim everywhere: `ORACLE PLACEBO: no factual verification was performed for this query. This message contains no evidence about the world document.`
- Gate thresholds, verbatim (frozen; Task 13): clean Δfew ≥ 4pp with cluster-bootstrap 95% CI excluding 0 → harm survives; ≤ 2pp with CI including 0 → mostly harness artifact; 2–4pp indeterminate. Attribution: BOTH−CLEAN ≥ 3.5pp or > 50% of the original 7.2pp → headline mostly harness-induced. PLACEBO within 2pp of CLEAN → deliberation/turn-count effect.
- Default budgets: clean `[0,1,2,5]`; both `[1,2,5]`; placebo `[1,2,5]`; na_only `[1,2]`; doubled_only `[1,2]`; legacy `[1,2]`. Default replicates K=2 (legacy K=1).
- `data/judgments.jsonl` and `data/transcripts.jsonl` are inputs; never rewrite them.

---

### Task 1: Report prose corrections + data legacy notice

**Files:**
- Modify: `reports/2026-07-06-preliminary-findings.md`
- Create: `data/DATA.md` (data/ is untracked; the file still gets created)

**Interfaces:** none (prose). Verification is by grep.

- [ ] **Step 1: Apply the following exact edits to `reports/2026-07-06-preliminary-findings.md`** (old → new; each old string appears exactly once):

1. In the Correction block, after the "**Fix path:**" bullet, add a new bullet:
```markdown
- **Status update (2026-07-07):** the re-judge experiment is now designed and specced — see [`docs/superpowers/specs/2026-07-07-rejudge-harness-design.md`](../docs/superpowers/specs/2026-07-07-rejudge-harness-design.md) and the frozen pre-run protocol `docs/rejudge-protocol.md`.
```
2. Summary bullet — old:
```markdown
- **It is not a measurement artifact.** The inflation is symmetric across which side is correct, ruling out a known verdict-parsing bug. (The weak 8B judge *is* contaminated by a side bias and is treated as secondary.)
```
new:
```markdown
- **It is not the verdict-parse artifact.** The inflation is symmetric across which side is correct, ruling out the known default-to-Position-B parsing bug. *(Pre-audit scope: the two oracle-channel bugs are side-symmetric, so this check does NOT clear them — see Correction.)* (The weak 8B judge *is* contaminated by a side bias and is treated as secondary.)
```
3. Summary bullet — old:
```markdown
- **It is not mostly a simple oracle bug.** Across 54 harmful flips, only ~24% are direct oracle-answer errors; a comparable share are malformed judge *queries*; and **~46% is the judge over-updating on *correct* verification** — a reasoning failure a better oracle will not fix.
```
new:
```markdown
- *(Pre-audit; reset to unknown — see Correction.)* The mechanism split measured on the corrupted oracle channel was: ~24% direct oracle-answer errors, ~26% malformed judge queries, ~46% over-updating on confirmations the labelers scored as correct. None of these shares survive the audit.
```
(If the bullet's wording differs slightly, match on the leading `- **It is not mostly a simple oracle bug.**` and replace the whole bullet.)
4. Heading — old: `## 3. Analysis (pre-registered)` → new: `## 3. Analysis (pre-specified before recomputation)`; and add at the end of that section:
```markdown
> **Framing note (2026-07-07):** these contrasts and the gate were fixed *after* the session had already recomputed the published table and seen the qualitative U-shape on this same data (recorded in the design spec §1). They are pre-specified relative to the confirmatory recomputation, but post-hoc relative to first look. "Pre-registered" is reserved for the re-judge gates in `docs/rejudge-protocol.md`, which are genuinely ex-ante.
```
5. Old: `Pre-registered result (70B): **Δfew = +7.23 pp` → new: `Pre-specified result (70B): **Δfew = +7.23 pp`
6. Old: `- **Contrasts (percentage points):**` — leave unchanged. Old: `- **Gate to "bank" the harm claim:**` — leave unchanged.
7. §4.2 — old:
```markdown
The 70B inflation is symmetric across correct-side (and its format-noncompliance proxy is flat across budgets), so the effect is a genuine change in judge decisions, not a parsing artifact.
```
new:
```markdown
The 70B inflation is symmetric across correct-side (and its format-noncompliance proxy is flat across budgets), so the effect is not driven by the verdict-parse fallback. The two oracle-channel bugs are themselves side-symmetric, so this check does **not** clear them (see Correction).
```
8. §4.3 — old: `### 4.3 Mechanism: why the judge flips` → new:
```markdown
### 4.3 Mechanism: why the judge flips *(pre-audit — labels scored the corrupted oracle channel; retained for the record, reset to unknown)*
```
9. §4.4 — old: `### 4.4 Confidence goes the wrong way` → new: `### 4.4 Confidence goes the wrong way *(pre-audit, and near-degenerate)*`, and append to that section's paragraph:
```markdown
The confidence distribution is also nearly degenerate — 4 in 1,695/2,583 rows, 5 in 887, 3 exactly once — so the "rise" is a shift from 4s to 5s, not a calibrated signal.
```
10. §5 — after the last existing bullet in "## 5. Robustness & limitations", add:
```markdown
- **Turn-count confound:** budget>0 judgments insert extra conversation turns (query prompt, judge reply, oracle result — per query) before the verdict; budget-0 goes straight to verdict. A surviving clean-harness Δfew could therefore reflect deliberation/turn effects rather than verification content; the re-judge PLACEBO arm isolates this.
- **Budget-20 cell:** not a small random subsample but a truncated, single-world partial run — non-representative, not merely underpowered.
```
11. §6 — replace the whole "## 6. Bottom line" section body (old text starts `For a strong judge under a knowledge asymmetry` and ends `is the open question.`) with:
```markdown
What survives the harness audit is narrow: **in the original pilot implementation, adding a few oracle calls worsened the 70B judge's accuracy** (Δfew = +7.2 pp, CI [4.6, 10.2]). Whether that is a fact about *limited verification*, about *two specific harness bugs*, or about *extra deliberation turns* is exactly what the fixed-harness re-judge (CLEAN / bug-replay / PLACEBO arms, pre-registered gates in `docs/rejudge-protocol.md`) will decide. The mechanism split and the confidence trend are pre-audit observations about the corrupted pipeline and carry no interpretive weight until re-measured.
```
12. §7 — replace the whole section body (old starts `An **open-source judge × debater capability experiment**`) with:
```markdown
The next experiment is the **fixed-harness re-judge** of the same 318 transcripts (design: `docs/superpowers/specs/2026-07-07-rejudge-harness-design.md`; frozen protocol & gates: `docs/rejudge-protocol.md`): CLEAN {0,1,2,5}, bug-replay BOTH {1,2,5}, PLACEBO {1,2,5}, single-bug arms {1,2}, K=2 replicates, plus a legacy QA-replay subset. The open-source judge × debater capability grid (Llama/Qwen/Gemma ladders) remains designed but **gated** on the re-judge outcome and inherits the fixed harness.
```
13. Old: `run `uv run pytest` (40 tests)` → new: `run `uv run pytest``
14. §8 — old: `- **Generated outputs:** `analysis/output/report.md`` line: append `` `mechanism_cases.md`, `` after `` `mechanism_labels.md`, `` (i.e. add `mechanism_cases.md` to the list).

- [ ] **Step 2: Create `data/DATA.md`:**
```markdown
# Data provenance — READ BEFORE USING

`judgments.jsonl` (2,583 rows) and `transcripts.jsonl` (318 rows) are the **original pilot output,
generated by a harness with known data-corrupting bugs** (audit 2026-07-06). Keep for the record;
do not treat oracle-related fields as measurements of clean verification.

| Bug | Affected field | Effect |
|---|---|---|
| `NOT ADDRESSED`.startswith(`NO`) miscoding (judge.py:189) | `queries_submitted[].response` | every NOT ADDRESSED recorded/fed back as `NO`; 0 of 5,733 exchanges show NOT ADDRESSED |
| Query double-wrap (query_phase_prompt vs oracle template mismatch) | oracle-facing text (never persisted) | ~100% of oracle queries sent garbled; `queries_submitted[].query` stores the judge's PRE-doubling claim, NOT what the oracle saw |
| Default-to-Position-B on unparseable verdict (judge.py:85) | `verdict` | unparseable verdicts silently coded "Position B" |
| `int(raw[0])` confidence parse (judge.py:63) | `confidence` | first digit after `CONFIDENCE:` wins; distribution near-degenerate (4×1695, 5×887, 3×1) |
| A/B re-randomized per budget (judge.py:104-111) | `position_a_is_correct` | same transcript gets different A/B assignment at different budgets |

The fixed-harness re-judge writes new records to `rejudge/output/` with full raw logging.
Win-rate aggregates over `verdict_correct` remain valid *as descriptions of the buggy pilot*.
```
- [ ] **Step 3: Verify:** `grep -c "pre-registered" reports/2026-07-06-preliminary-findings.md` → expected `0` (case-sensitive; "Pre-registered"/"pre-registered" all replaced; the §3 framing-note mention of `"Pre-registered" is reserved` is inside quotes and acceptable — if grep shows only that line, PASS). `grep -c "ORACLE PLACEBO" data/DATA.md` → 0 (not there; sanity), `test -f data/DATA.md` → exists.
- [ ] **Step 4: Commit:** `git add reports/2026-07-06-preliminary-findings.md && git commit -m "Scope pre-audit claims inline; replace bottom line and next steps; add data provenance notice"` (DATA.md is inside untracked `data/`; that's fine — it travels with the data).

### Task 2: Dashboard corrections

**Files:**
- Modify: `reports/findings-dashboard.html`

**Interfaces:** none. The controller redeploys the Artifact after this task — do NOT attempt to publish from the subagent.

- [ ] **Step 1: Locate and fix the banked-claims footer.** Grep the file for `What we can bank`. In that block, replace the mechanism-split entry (the `<li>`/card whose text contains `oracle-answer errors` or `deep myopia` or `~46%`) with:
```html
<li><strong>Banked (narrow):</strong> in the original pilot implementation, a few oracle calls worsened the 70B judge's accuracy (Δfew +7.2pp, CI [4.6,10.2]). Everything interpretive — mechanism split, deep-myopia share, confidence trend — is reset pending the fixed-harness re-judge.</li>
```
Remove any other footer entry that asserts the mechanism split or "not a harness artifact" as banked.
- [ ] **Step 2: Fix the artifact-check tile.** Grep for `70B clean`. Replace the tile label/value with `Not the parse-fallback artifact` and set its caption/subtitle to `side-symmetry check — does NOT clear the two side-symmetric oracle bugs`.
- [ ] **Step 3: Sweep captions.** Grep for `not a measurement artifact` and `not an artifact` (case-insensitive) anywhere in chart captions/body text; rescope each occurrence to `not the verdict-parse artifact (oracle bugs not cleared — see correction)`.
- [ ] **Step 4: Verify:** `grep -ci "not a measurement artifact" reports/findings-dashboard.html` → 0; `grep -c "Not the parse-fallback artifact" reports/findings-dashboard.html` → ≥1; open-tag hygiene: `python -c "import html.parser,io;p=html.parser.HTMLParser();p.feed(open('reports/findings-dashboard.html',encoding='utf-8').read());print('parsed ok')"` → `parsed ok`.
- [ ] **Step 5: Commit:** `git add reports/findings-dashboard.html && git commit -m "Rescope dashboard banked-claims footer and artifact-check tile to post-audit state"`

### Task 3: run_report.py mechanism correction + pass-2 fold-in, regenerate outputs

**Files:**
- Modify: `analysis/run_report.py`
- Modify: `analysis/output/mechanism_validation.md` (prepend header)
- Test: `tests/test_report.py` (add cases; existing tests must keep passing)

**Interfaces:**
- Consumes: `analysis.mechanism.summarize_labels(labels)` (existing), `analysis/output/labels.csv`, `analysis/output/labels_pass2.csv` (CSV rows with a `label` column; pass2 also has one row per case aligned by `case_id` — both CSVs contain columns `case_id,label` at minimum; verify with `head -1 analysis/output/labels.csv`).
- Produces: `build_report(df, B, seed, labels=None, labels2=None)` — new optional `labels2` kwarg; `_kappa(a: list[str], b: list[str]) -> float`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_report.py`:
```python
def test_kappa_perfect_and_chance():
    from analysis.run_report import _kappa
    assert _kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # 50/50 marginals, agreement at chance level -> kappa ~ 0
    assert abs(_kappa(["a", "b"] * 10, ["a"] * 10 + ["b"] * 10)) < 0.15


def test_mechanism_section_carries_correction_and_pass2(monkeypatch):
    import pandas as pd
    from analysis import run_report
    df = _tiny_df()  # reuse the existing fixture helper in this test file; if named differently, use that one
    labels = [{"case_id": "c1", "label": "FM1"}, {"case_id": "c2", "label": "FM2"}]
    labels2 = [{"case_id": "c1", "label": "FM1"}, {"case_id": "c2", "label": "other"}]
    text = run_report.build_report(df, B=10, seed=0, labels=labels, labels2=labels2)
    assert "corrupted" in text.lower()          # correction framing present
    assert "kappa" in text.lower() or "κ" in text
    assert "Deliverable D" not in text          # stale recommendation gone
    assert "mechanism-label validation ($0)" not in text
```
(Adapt `_tiny_df()` to whatever minimal-DataFrame helper the file already uses; if none exists, build the smallest DataFrame accepted by `inference.summarize` by copying the setup of the existing `build_report` test in this file.)
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_report.py -q` — expected: new tests FAIL (no `_kappa`, no `labels2` kwarg).
- [ ] **Step 3: Implement in `analysis/run_report.py`:**
  1. Add:
```python
def _kappa(a, b):
    labs = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return 1.0 if pe >= 1 else (po - pe) / (1 - pe)
```
  2. `build_report(df, B=10000, seed=0, labels=None, labels2=None)`. Replace the mechanism block:
```python
    if labels is not None:
        parts.append("\n\n## Mechanism (pre-audit — corrupted oracle channel)\n\n"
                     "> **Correction (2026-07-06):** the pilot's oracle pipeline was broken for ~100% of "
                     "calls (NOT-ADDRESSED→NO miscoding; doubled queries). These labels scored the corrupted "
                     "channel and are a postmortem of the buggy harness, NOT a decomposition of clean "
                     "verification. Reset to unknown pending the fixed-harness re-judge "
                     "(`docs/rejudge-protocol.md`).\n\n"
                     + _md_table(mechanism.summarize_labels(labels)))
        if labels2 is not None:
            a = [r["label"] for r in labels]
            b = [r["label"] for r in labels2]
            agree = sum(x == y for x, y in zip(a, b))
            parts.append(f"\n\nTwo-pass agreement: {agree}/{len(a)} (Cohen's kappa = {_kappa(a, b):.2f}). "
                         "Full consensus + refined O1/Q1/R1/R2 taxonomy: `mechanism_validation.md` "
                         "(same correction applies).")
```
  3. In `_recommendation`, replace everything from `if labels is not None:` through the end of the "Recommended next steps" append with:
```python
    if labels is not None:
        out.append("- **Mechanism labels (pre-audit):** postmortem of the corrupted oracle channel — "
                   "no longer treated as a decomposition of clean verification; carry no interpretive "
                   "weight until re-measured under the fixed harness.\n")
    out.append("- **Next step (designed, spend-gated):** the fixed-harness re-judge — CLEAN {0,1,2,5}, "
               "bug-replay BOTH {1,2,5}, PLACEBO {1,2,5}, single-bug arms {1,2}, K=2 replicates, legacy "
               "QA subset. Ex-ante gates frozen in `docs/rejudge-protocol.md`. The capability grid stays "
               "gated on that outcome.\n"
               "- Audit trail: `mechanism_labels.md`, `mechanism_validation.md`, `labels.csv`, "
               "`labels_pass2.csv`.\n")
```
  4. In `main()`, load pass-2 next to pass-1 and pass it through:
```python
    labels2 = None
    labels2_csv = out / "labels_pass2.csv"
    if labels2_csv.exists():
        import csv
        with open(labels2_csv, encoding="utf-8") as f:
            labels2 = list(csv.DictReader(f))
    (out / "report.md").write_text(build_report(df, labels=labels, labels2=labels2), encoding="utf-8")
```
  5. Also update the "Primary inference (70B)" heading string: `"Pre-registered contrasts (pp), "` → `"Pre-specified contrasts (pp; see report framing note), "`.
- [ ] **Step 4: Prepend to `analysis/output/mechanism_validation.md`** (file edit, first lines):
```markdown
> **⚠️ Correction (2026-07-06):** this validation was performed on the CORRUPTED pilot oracle channel
> (NOT-ADDRESSED→NO miscoding, doubled queries — see the report Correction). It validates label
> *reliability* (two-pass κ=0.72), not the mechanism *conclusion*. The O1/Q1/R1/R2 shares are a
> postmortem of the buggy harness and reset to unknown pending the fixed-harness re-judge.

```
- [ ] **Step 5: Run tests:** `uv run pytest tests/test_report.py -q` → all pass.
- [ ] **Step 6: Regenerate:** `uv run python -m analysis.run_report` → check `analysis/output/report.md` contains "corrupted oracle channel" and kappa line; `grep -c "Deliverable D\|mechanism-label validation" analysis/output/report.md` → 0.
- [ ] **Step 7: Commit:** `git add analysis/run_report.py analysis/output/ tests/test_report.py && git commit -m "Fold pass-2 validation into report with correction framing; retire stale recommendations"`

### Task 4: Harden the strict verdict parser

**Files:**
- Modify: `analysis/infra/parsing.py`
- Test: `tests/test_infra.py`

**Interfaces:**
- Produces: `parse_verdict_strict(text) -> dict(verdict='A'|'B'|None, confidence=int|None, reasoning=str, parse_ok=bool, raw=str)` — same signature, hardened; `normalize_oracle` gains a `NO EVIDENCE → INVALID` rule. All 13 existing tests in `tests/test_infra.py` must keep passing unchanged.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_infra.py`:
```python
def test_verdict_negation_and_ambiguity_are_invalid():
    # review finding: hedged/negated lines mis-parsed with parse_ok=True
    assert parsing.parse_verdict_strict("VERDICT: Not Position A")["verdict"] is None
    assert parsing.parse_verdict_strict("VERDICT: Position A or Position B")["verdict"] is None
    assert parsing.parse_verdict_strict("VERDICT: Neither position")["verdict"] is None


def test_verdict_markdown_and_blockquote_leads():
    assert parsing.parse_verdict_strict("**VERDICT:** Position A")["verdict"] == "A"
    assert parsing.parse_verdict_strict("> VERDICT: B.")["verdict"] == "B"
    assert parsing.parse_verdict_strict("**VERDICT: Position B**")["verdict"] == "B"


def test_verdict_on_next_line():
    assert parsing.parse_verdict_strict("VERDICT:\nPosition A\nCONFIDENCE: 4")["verdict"] == "A"


def test_reasoning_multiline():
    r = parsing.parse_verdict_strict(
        "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: first line\nsecond line\n\nthird line")
    assert "second line" in r["reasoning"] and "third line" in r["reasoning"]


def test_confidence_markdown_bold():
    assert parsing.parse_verdict_strict("VERDICT: A\nCONFIDENCE: **4**")["confidence"] == 4


def test_oracle_no_evidence_is_invalid():
    # 'NO EVIDENCE' is closer to NOT ADDRESSED than to a contradicting NO
    assert parsing.normalize_oracle("NO EVIDENCE in the text supports this") == "INVALID"
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_infra.py -q` — new tests FAIL.
- [ ] **Step 3: Replace `parse_verdict_strict` in `analysis/infra/parsing.py` with:**
```python
def _commit_side(v):
    """Return 'A'/'B' only when the text commits to exactly one side; else None."""
    v = v.strip().lstrip(_LEAD).strip()
    if re.match(r"(NOT|NEITHER|NO)\b", v):
        return None
    a = bool(re.search(r"\b(?:POSITION|DEBATER)\s+A\b", v)) or bool(re.match(r"A\b", v))
    b = bool(re.search(r"\b(?:POSITION|DEBATER)\s+B\b", v)) or bool(re.match(r"B\b", v))
    if a and b:
        return None
    return "A" if a else ("B" if b else None)


def parse_verdict_strict(text):
    """Parse a judge verdict block. NO silent default.

    Returns dict:
      verdict: 'A' | 'B' | None   (None => unparseable/ambiguous/negated)
      confidence: int 1-5 | None
      reasoning: str (multi-line: runs until the next KEY: line)
      parse_ok: bool              (True iff a verdict side was found)
      raw: str
    """
    text = text or ""
    verdict = None
    confidence = None
    reasoning_lines = []
    capturing = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip().lstrip(_LEAD).strip()
        up = s.upper()
        if up.startswith("VERDICT:"):
            capturing = False
            v = up[len("VERDICT:"):]
            if not v.strip().lstrip(_LEAD).strip() and i + 1 < len(lines):
                v = lines[i + 1].strip().lstrip(_LEAD).strip().upper()
            verdict = _commit_side(v)
        elif up.startswith("CONFIDENCE:"):
            capturing = False
            m = re.match(r"[\s*_`]*([1-5])\b", s[len("CONFIDENCE:"):])
            if m:
                confidence = int(m.group(1))
        elif up.startswith("REASONING:"):
            capturing = True
            reasoning_lines.append(s[len("REASONING:"):].strip())
        elif capturing:
            reasoning_lines.append(line.strip())
    reasoning = "\n".join(reasoning_lines).strip()
    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning,
            "parse_ok": verdict is not None, "raw": text}
```
And in `normalize_oracle`, insert immediately before the `if re.match(r"NO\b", t):` line:
```python
    if re.match(r"NO\s+EVIDENCE\b", t):
        return "INVALID"
```
- [ ] **Step 4: Run the full infra suite:** `uv run pytest tests/test_infra.py -q` → all pass (existing 13 + 6 new). Then `uv run pytest -q` → whole suite green.
- [ ] **Step 5: Commit:** `git add analysis/infra/parsing.py tests/test_infra.py && git commit -m "Harden strict verdict parser: negation/ambiguity, markdown leads, next-line verdicts, multiline reasoning"`

### Task 5: rejudge package scaffold + pilot-compat parser port

**Files:**
- Create: `rejudge/__init__.py` (empty), `rejudge/parsers.py`
- Test: `tests/test_rejudge_parsers.py`

**Interfaces:**
- Consumes: `analysis.infra.parsing.parse_verdict_strict`.
- Produces: `rejudge.parsers.PARSER_VERSION = "2.0.0"`; `parse_pilot_compat(text: str) -> dict(verdict: "Position A"|"Position B", confidence: int, reasoning: str)`; `parse_both(text: str) -> dict(strict=<parse_verdict_strict dict>, pilot=<parse_pilot_compat dict>, parser_version=PARSER_VERSION)`.

- [ ] **Step 1: Write the failing test** — `tests/test_rejudge_parsers.py`:
```python
from rejudge import parsers


def test_pilot_compat_default_to_b():
    r = parsers.parse_pilot_compat("I can't decide, both sound plausible.")
    assert r["verdict"] == "Position B"          # the pilot's silent default


def test_pilot_compat_first_digit_confidence_bug():
    r = parsers.parse_pilot_compat("VERDICT: Position A\nCONFIDENCE: 10% unsure, 4")
    assert r["confidence"] == 1                  # int(raw[0]) -> 1, faithfully wrong


def test_pilot_compat_fallback_scan():
    r = parsers.parse_pilot_compat("blah blah I lean Position A overall")
    assert r["verdict"] == "Position A"          # full-text scan fallback


def test_parse_both_carries_version_and_disagreement():
    out = parsers.parse_both("gibberish with no verdict")
    assert out["parser_version"] == parsers.PARSER_VERSION
    assert out["strict"]["verdict"] is None       # strict: INVALID
    assert out["pilot"]["verdict"] == "Position B" # pilot: coerced
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_parsers.py -q` — FAIL (module not found).
- [ ] **Step 3: Implement `rejudge/parsers.py`:**
```python
"""Dual verdict parsing: hardened strict parser + a faithful port of the pilot parser.

The pilot parser (judge.py:_parse_verdict) is ported bug-for-bug — default-to-Position-B,
int(raw[0]) confidence, full-text fallback scan — because every arm parses every raw verdict
BOTH ways (measurement-side comparison at zero API cost).
"""
from __future__ import annotations

from analysis.infra.parsing import parse_verdict_strict

PARSER_VERSION = "2.0.0"


def parse_pilot_compat(text: str) -> dict:
    verdict = ""
    confidence = 1
    reasoning = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped[len("VERDICT:"):].strip()
        elif stripped.upper().startswith("CONFIDENCE:"):
            raw = stripped[len("CONFIDENCE:"):].strip()
            try:
                confidence = max(1, min(5, int(raw[0])))
            except (ValueError, IndexError):
                confidence = 1
        elif stripped.upper().startswith("REASONING:"):
            reasoning = stripped[len("REASONING:"):].strip()
    verdict_upper = verdict.upper()
    if "POSITION A" in verdict_upper:
        verdict = "Position A"
    elif "POSITION B" in verdict_upper:
        verdict = "Position B"
    else:
        text_upper = text.upper()
        if "POSITION A" in text_upper:
            verdict = "Position A"
        elif "POSITION B" in text_upper:
            verdict = "Position B"
        else:
            verdict = "Position B"  # pilot behavior: default to B when unparseable
    if not reasoning:
        reasoning = text[:500]
    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning}


def parse_both(text: str) -> dict:
    return {"strict": parse_verdict_strict(text),
            "pilot": parse_pilot_compat(text),
            "parser_version": PARSER_VERSION}
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_parsers.py -q` → pass.
- [ ] **Step 5: Commit:** `git add rejudge/ tests/test_rejudge_parsers.py && git commit -m "Add rejudge package with dual verdict parsing (strict + pilot-compat port)"`

### Task 6: Query composers (clean + pilot port)

**Files:**
- Create: `rejudge/composer.py`
- Test: `tests/test_rejudge_composer.py`

**Interfaces:**
- Produces: `pilot_extract_claim(query_response: str) -> str` (faithful port incl. dead strip); `clean_extract_claim(query_response: str) -> tuple[str, bool]` (claim, well_formed); `compose_oracle_prompt(template: str, world_document: str, claim: str) -> str`.
- The oracle user template (from `experiment_protocol.json`, `oracle.user_prompt_template`) contains `QUERY: Is it supported by the text that {query_claim}` — both arms use `compose_oracle_prompt`; arm difference is entirely in claim extraction.

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_composer.py`:
```python
import json

from rejudge import composer

TEMPLATE = ("WORLD DOCUMENT:\n{world_document}\n\n"
            "QUERY: Is it supported by the text that {query_claim}\n\n"
            "Respond with exactly one of: YES, NO, or NOT ADDRESSED.")


def test_pilot_port_reproduces_doubling():
    # the judge follows its instruction: interrogative phrasing the strip does NOT handle
    q = "Is it stated in the text that the treaty was signed in Year 38?"
    claim = composer.pilot_extract_claim(q)
    assert claim == q                                    # strip never fires (the bug)
    prompt = composer.compose_oracle_prompt(TEMPLATE, "DOC", claim)
    assert "Is it supported by the text that Is it stated in the text that" in prompt


def test_pilot_port_strip_fires_on_its_own_phrase():
    q = "Is it supported by the text that the treaty was signed?"
    assert composer.pilot_extract_claim(q) == "the treaty was signed?"


def test_clean_extract_claim_prefixed():
    claim, ok = composer.clean_extract_claim("CLAIM: the treaty was signed in Year 38")
    assert claim == "the treaty was signed in Year 38" and ok is True


def test_clean_extract_tolerates_interrogative_scaffold():
    claim, ok = composer.clean_extract_claim("Is it stated in the text that the king died?")
    assert claim == "the king died" and ok is False


def test_clean_never_doubles():
    claim, _ = composer.clean_extract_claim("Is it supported by the text that the king died?")
    prompt = composer.compose_oracle_prompt(TEMPLATE, "DOC", claim)
    assert prompt.count("Is it supported by the text that") == 1


def test_pilot_port_matches_real_data_shape():
    # stored queries in data/judgments.jsonl are PRE-doubling claims; wrapping one that starts
    # with an interrogative must reproduce the garble the audit found
    rows = [json.loads(l) for l in open("data/judgments.jsonl", encoding="utf-8")]
    qs = [e["query"] for r in rows for e in r["queries_submitted"]]
    interrogative = [q for q in qs if q.lower().startswith("is it ")]
    assert interrogative, "expected interrogative stored queries in pilot data"
    p = composer.compose_oracle_prompt(TEMPLATE, "DOC", composer.pilot_extract_claim(interrogative[0]))
    assert "Is it supported by the text that Is it" in p
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_composer.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/composer.py`:**
```python
"""Oracle-query composition: the pilot's buggy path (ported faithfully) and the clean path.

Pilot bug being reproduced/fixed: the judge is INSTRUCTED to phrase queries as
"Is it stated in the text that X?" while the oracle template wraps its input in
"Is it supported by the text that {query_claim}" and the strip only removes the
latter phrasing — so ~100% of pilot oracle queries were doubled. CLEAN passes a
bare claim exactly once.
"""
from __future__ import annotations

import re

PILOT_STRIP_PREFIXES = ("Is it supported by the text that ",
                        "is it supported by the text that ")

_SCAFFOLD = re.compile(r"(?i)^\s*is it (?:stated|supported)\s+(?:in|by)\s+the text that\s+")
_CLAIM = re.compile(r"(?is)^\s*claim\s*:\s*(.+)$")


def pilot_extract_claim(query_response: str) -> str:
    """Faithful port of judge.py:162-167 (incl. its ineffectiveness)."""
    claim = query_response.strip()
    for prefix in PILOT_STRIP_PREFIXES:
        if claim.startswith(prefix):
            claim = claim[len(prefix):]
            break
    return claim


def clean_extract_claim(query_response: str) -> tuple[str, bool]:
    """Extract a bare declarative claim. Returns (claim, well_formed).

    well_formed=True iff the judge followed the CLEAN instruction ("CLAIM: ...").
    Interrogative scaffolds are tolerated (stripped) but flagged well_formed=False.
    """
    s = query_response.strip()
    m = _CLAIM.match(s)
    if m:
        return m.group(1).strip().rstrip("?").strip(), True
    s = _SCAFFOLD.sub("", s).strip().rstrip("?").strip()
    return s, False


def compose_oracle_prompt(template: str, world_document: str, claim: str) -> str:
    return template.format(world_document=world_document, query_claim=claim)
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_composer.py -q` → pass.
- [ ] **Step 5: Commit:** `git add rejudge/composer.py tests/test_rejudge_composer.py && git commit -m "Add clean and pilot-port query composers with doubling reproduction tests"`

### Task 7: Oracle normalizers + DONE detectors

**Files:**
- Create: `rejudge/oracle_channel.py`
- Test: `tests/test_rejudge_oracle.py`

**Interfaces:**
- Consumes: `analysis.infra.parsing.normalize_oracle` (strict).
- Produces: `normalize_pilot(text: str) -> str` ("YES"|"NO"|"NOT ADDRESSED", faithful port incl. the NA→NO bug); `normalize_strict(text: str) -> str` (delegates); `is_done_pilot(text: str) -> bool`; `is_done_robust(text: str) -> bool`.

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_oracle.py`:
```python
from rejudge import oracle_channel as oc


def test_pilot_normalizer_na_to_no_bug():
    # "NOT ADDRESSED".startswith("NO") is True -> the pilot recorded NO (judge.py:189)
    assert oc.normalize_pilot("NOT ADDRESSED") == "NO"
    assert oc.normalize_pilot("YES") == "YES"
    assert oc.normalize_pilot("NO") == "NO"
    assert oc.normalize_pilot("The text is unclear.") == "NOT ADDRESSED"  # pilot else-branch


def test_strict_normalizer_preserves_na():
    assert oc.normalize_strict("NOT ADDRESSED") == "NOT ADDRESSED"
    assert oc.normalize_strict("The text is unclear.") == "INVALID"


def test_done_detectors():
    assert oc.is_done_pilot("DONE") is True
    assert oc.is_done_pilot("I'm done.") is False        # the pilot brittleness, kept
    assert oc.is_done_robust("DONE") is True
    assert oc.is_done_robust("done.") is True
    assert oc.is_done_robust("I'm done — no further queries.") is True
    assert oc.is_done_robust("No more queries needed") is True
    assert oc.is_done_robust("Is it stated that the work is done?") is False
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_oracle.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/oracle_channel.py`:**
```python
"""Oracle-reply normalization (pilot port + strict) and DONE detection."""
from __future__ import annotations

import re

from analysis.infra.parsing import normalize_oracle as _strict


def normalize_pilot(text: str) -> str:
    """Faithful port of judge.py:186-192 — including NOT ADDRESSED -> NO."""
    t = text.strip().upper()
    if t.startswith("YES"):
        return "YES"
    if t.startswith("NO"):          # catches "NOT ADDRESSED" too: the bug
        return "NO"
    return "NOT ADDRESSED"


def normalize_strict(text: str) -> str:
    return _strict(text)


def is_done_pilot(text: str) -> bool:
    """Faithful port of judge.py:93-94."""
    return text.strip().upper() == "DONE"


_DONE_ROBUST = re.compile(
    r"(?i)^\W*(?:i\s*'?\s*a?m\s+)?done\b|^\W*no\s+(?:more|further)\s+quer",
)


def is_done_robust(text: str) -> bool:
    return bool(_DONE_ROBUST.search(text.strip()[:80])) and not text.strip().endswith("?")
```
(If `is_done_robust("I'm done — no further queries.")` fails on the em-dash, adjust the regex to search the whole first line rather than anchor: the tests define the contract.)
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_oracle.py -q` → pass; fix regex until the test-defined contract holds.
- [ ] **Step 5: Commit:** `git add rejudge/oracle_channel.py tests/test_rejudge_oracle.py && git commit -m "Add oracle normalizers (strict + pilot NA-to-NO port) and DONE detectors"`

### Task 8: Arms, protocol loading, seeds

**Files:**
- Create: `rejudge/config.py`
- Test: `tests/test_rejudge_config.py`

**Interfaces:**
- Consumes: `experiment_protocol.json` (keys: `judge.system_prompt`, `judge.user_prompt_template`, `judge.query_phase_prompt`, `judge.verdict_prompt`, `oracle.system_prompt`, `oracle.user_prompt_template`, `protocol.models.oracle`, `protocol.temperature.judge`, `protocol.temperature.oracle`); `analysis.infra.design.position_a_is_correct(question_id, transcript_index)`.
- Produces:
  - `ArmSpec` frozen dataclass: `name, oracle_normalizer("strict"|"pilot"), composer("clean"|"pilot"), done_detector("robust"|"pilot"), placebo: bool = False, randomize_ab_per_budget: bool = False, parser_primary: str = "strict"`.
  - `ARMS: dict[str, ArmSpec]` with keys `clean, both, placebo, na_only, doubled_only, legacy` (per the spec's arm table).
  - `DEFAULT_BUDGETS: dict[str, list[int]]` (global constraints), `DEFAULT_REPLICATES = 2`, `PLACEBO_TEXT` (verbatim global constraint), `JUDGE_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"`.
  - `load_protocol(path="experiment_protocol.json") -> dict`.
  - `clean_query_phase_prompt(pilot_prompt: str) -> str` — raises `ValueError` if the anchor line is missing.
  - `make_seed(*parts) -> int` (md5 port of judge.py:11-13), `judgment_seed(question_id, transcript_index, judge_model, budget, arm_name, replicate) -> int`.
  - `position_for(arm: ArmSpec, question_id, transcript_index, judge_model, budget) -> bool` — fixed A/B via `analysis.infra.design.position_a_is_correct` unless `arm.randomize_ab_per_budget`, in which case the pilot's per-budget draw (`random.Random(make_seed(qid, tidx, judge_model, budget)).choice([True, False])`).

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_config.py`:
```python
import random

from rejudge import config


def test_arms_table():
    assert set(config.ARMS) == {"clean", "both", "placebo", "na_only", "doubled_only", "legacy"}
    both = config.ARMS["both"]
    assert (both.oracle_normalizer, both.composer, both.done_detector) == ("pilot", "pilot", "pilot")
    na = config.ARMS["na_only"]
    assert (na.oracle_normalizer, na.composer) == ("pilot", "clean")
    dbl = config.ARMS["doubled_only"]
    assert (dbl.oracle_normalizer, dbl.composer) == ("strict", "pilot")
    assert config.ARMS["placebo"].placebo is True
    assert config.ARMS["legacy"].randomize_ab_per_budget is True
    assert config.ARMS["legacy"].parser_primary == "pilot"
    assert config.DEFAULT_BUDGETS["clean"] == [0, 1, 2, 5]
    assert config.DEFAULT_BUDGETS["both"] == [1, 2, 5]


def test_protocol_loads_real_file():
    p = config.load_protocol()
    assert "{query_claim}" in p["oracle"]["user_prompt_template"]
    assert "Is it stated in the text that" in p["judge"]["query_phase_prompt"]


def test_clean_query_prompt_rewrites_phrasing():
    p = config.load_protocol()
    out = config.clean_query_phase_prompt(p["judge"]["query_phase_prompt"])
    assert "Is it stated in the text that" not in out
    assert 'CLAIM: ' in out


def test_clean_query_prompt_raises_on_drift():
    try:
        config.clean_query_phase_prompt("some unrelated template")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_ab_fixed_across_budgets_for_nonlegacy():
    arm = config.ARMS["clean"]
    a1 = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 1)
    a2 = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 5)
    assert a1 == a2


def test_legacy_matches_pilot_randomization():
    arm = config.ARMS["legacy"]
    got = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2)
    seed = config.make_seed("CN-001", 1, config.JUDGE_MODEL, 2)
    assert got == random.Random(seed).choice([True, False])


def test_replicates_change_seed_but_not_position():
    arm = config.ARMS["clean"]
    s1 = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 2, "clean", 0)
    s2 = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 2, "clean", 1)
    assert s1 != s2
    assert (config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2)
            == config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2))
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_config.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/config.py`:**
```python
"""Arm definitions, protocol loading, and the seed scheme for the re-judge."""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from analysis.infra.design import position_a_is_correct

JUDGE_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
DEFAULT_REPLICATES = 2
PLACEBO_TEXT = ("ORACLE PLACEBO: no factual verification was performed for this query. "
                "This message contains no evidence about the world document.")

_PILOT_PHRASING_LINE = '- Be phrased in the form: "Is it stated in the text that [specific factual claim]?"'
_CLEAN_PHRASING_LINE = ('- Be given as ONLY the bare factual claim on a single line, prefixed exactly '
                        '"CLAIM: " — do NOT phrase it as a question')


@dataclass(frozen=True)
class ArmSpec:
    name: str
    oracle_normalizer: str          # "strict" | "pilot"
    composer: str                   # "clean" | "pilot"
    done_detector: str              # "robust" | "pilot"
    placebo: bool = False
    randomize_ab_per_budget: bool = False   # LEGACY replay only
    parser_primary: str = "strict"          # LEGACY: "pilot"


ARMS = {
    "clean": ArmSpec("clean", "strict", "clean", "robust"),
    "both": ArmSpec("both", "pilot", "pilot", "pilot"),
    "placebo": ArmSpec("placebo", "strict", "clean", "robust", placebo=True),
    "na_only": ArmSpec("na_only", "pilot", "clean", "robust"),
    "doubled_only": ArmSpec("doubled_only", "strict", "pilot", "robust"),
    "legacy": ArmSpec("legacy", "pilot", "pilot", "pilot",
                      randomize_ab_per_budget=True, parser_primary="pilot"),
}

DEFAULT_BUDGETS = {
    "clean": [0, 1, 2, 5],
    "both": [1, 2, 5],
    "placebo": [1, 2, 5],
    "na_only": [1, 2],
    "doubled_only": [1, 2],
    "legacy": [1, 2],
}


def load_protocol(path: str = "experiment_protocol.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_query_phase_prompt(pilot_prompt: str) -> str:
    """Rewrite the pilot's query-phrasing instruction to the CLEAN bare-claim form.

    Raises ValueError if the anchor line is missing (protocol drift guard).
    """
    if _PILOT_PHRASING_LINE not in pilot_prompt:
        raise ValueError("pilot query_phase_prompt anchor line not found — protocol drifted?")
    return pilot_prompt.replace(_PILOT_PHRASING_LINE, _CLEAN_PHRASING_LINE)


def make_seed(*parts) -> int:
    """Port of judge.py:make_seed."""
    key = "|".join(str(p) for p in parts)
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def judgment_seed(question_id, transcript_index, judge_model, budget, arm_name, replicate) -> int:
    return make_seed(question_id, transcript_index, judge_model, budget, arm_name, replicate)


def position_for(arm: ArmSpec, question_id, transcript_index, judge_model, budget) -> bool:
    if arm.randomize_ab_per_budget:
        seed = make_seed(question_id, transcript_index, judge_model, budget)
        return random.Random(seed).choice([True, False])
    return position_a_is_correct(question_id, transcript_index)
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_config.py -q` → pass. If `test_clean_query_prompt_rewrites_phrasing` fails because the real protocol line differs, print the actual `query_phase_prompt` (`uv run python -c "from rejudge.config import load_protocol; print(load_protocol()['judge']['query_phase_prompt'])"`), set `_PILOT_PHRASING_LINE` to the exact line found, and re-run — the anchor must match the real file byte-for-byte.
- [ ] **Step 5: Commit:** `git add rejudge/config.py tests/test_rejudge_config.py && git commit -m "Add arm specs, protocol loader with clean query-prompt rewrite, and seed scheme"`

### Task 9: Record schema + provenance

**Files:**
- Create: `rejudge/records.py`
- Test: `tests/test_rejudge_records.py`

**Interfaces:**
- Produces: `get_git_sha() -> str` (short SHA or "unknown"); `utc_now_iso() -> str`; `build_record(*, transcript: dict, arm, budget: int, replicate: int, position_a_is_correct: bool, exchanges: list[dict], raw_verdict_text: str, parses: dict, judge_messages: list[dict], seed: int, judge_model: str, oracle_model: str, dry_run: bool, queries_used: int) -> dict`. `parses` is the `parse_both` output. Derived fields: `verdict_correct_strict: bool|None` (None when strict verdict is None), `verdict_correct_pilot: bool`, `cell_key: str` = `"{arm}|{question_id}|{transcript_index}|{budget}|{replicate}"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_records.py`:
```python
from rejudge import records
from rejudge.config import ARMS
from rejudge.parsers import parse_both

TR = {"question_id": "CN-001", "transcript_index": 1, "world": "carath_norn"}


def _rec(verdict_text, pos_a=True):
    return records.build_record(
        transcript=TR, arm=ARMS["clean"], budget=2, replicate=0,
        position_a_is_correct=pos_a, exchanges=[], raw_verdict_text=verdict_text,
        parses=parse_both(verdict_text), judge_messages=[{"role": "user", "content": "x"}],
        seed=123, judge_model="j", oracle_model="o", dry_run=True, queries_used=0)


def test_provenance_fields_present():
    r = _rec("VERDICT: Position A\nCONFIDENCE: 4")
    for f in ["harness_version", "arm", "dry_run", "created_at", "parser_version",
              "seed", "judge_model", "oracle_model", "budget", "replicate", "cell_key"]:
        assert f in r, f
    assert r["dry_run"] is True
    assert r["cell_key"] == "clean|CN-001|1|2|0"


def test_verdict_correct_both_parses():
    r = _rec("VERDICT: Position A\nCONFIDENCE: 4", pos_a=True)
    assert r["verdict_correct_strict"] is True and r["verdict_correct_pilot"] is True
    r2 = _rec("total gibberish", pos_a=True)
    assert r2["verdict_correct_strict"] is None            # INVALID, never coerced
    assert r2["verdict_correct_pilot"] is False            # pilot coerces to B -> wrong


def test_raw_text_persisted():
    r = _rec("VERDICT: Position B")
    assert r["raw_verdict_text"] == "VERDICT: Position B"
    assert r["judge_messages"][0]["content"] == "x"
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_records.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/records.py`:**
```python
"""Output record construction with full provenance."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def get_git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


_GIT_SHA = None


def _sha() -> str:
    global _GIT_SHA
    if _GIT_SHA is None:
        _GIT_SHA = get_git_sha()
    return _GIT_SHA


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_record(*, transcript, arm, budget, replicate, position_a_is_correct,
                 exchanges, raw_verdict_text, parses, judge_messages, seed,
                 judge_model, oracle_model, dry_run, queries_used) -> dict:
    strict = parses["strict"]
    pilot = parses["pilot"]
    if strict["verdict"] is None:
        correct_strict = None
    else:
        correct_strict = (strict["verdict"] == "A") == position_a_is_correct
    correct_pilot = (pilot["verdict"] == "Position A") == position_a_is_correct
    qid = transcript["question_id"]
    tidx = transcript["transcript_index"]
    return {
        "question_id": qid,
        "transcript_index": tidx,
        "world": transcript.get("world"),
        "arm": arm.name,
        "budget": budget,
        "replicate": replicate,
        "cell_key": f"{arm.name}|{qid}|{tidx}|{budget}|{replicate}",
        "position_a_is_correct": position_a_is_correct,
        "queries_used": queries_used,
        "exchanges": exchanges,
        "raw_verdict_text": raw_verdict_text,
        "verdict_strict": strict,
        "verdict_pilot": pilot,
        "verdict_correct_strict": correct_strict,
        "verdict_correct_pilot": correct_pilot,
        "judge_messages": judge_messages,
        "seed": seed,
        "judge_model": judge_model,
        "oracle_model": oracle_model,
        "parser_version": parses["parser_version"],
        "harness_version": _sha(),
        "dry_run": dry_run,
        "created_at": utc_now_iso(),
    }
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_records.py -q` → pass.
- [ ] **Step 5: Commit:** `git add rejudge/records.py tests/test_rejudge_records.py && git commit -m "Add provenance-tagged record schema with dual verdict-correctness"`

### Task 10: API client with retries, cost cap, context guard, dry-run tagging

**Files:**
- Create: `rejudge/api_client.py`
- Test: `tests/test_rejudge_client.py`

**Interfaces:**
- Produces: `CapExceededError(RuntimeError)`, `ContextGuardError(RuntimeError)`; class `RejudgeClient(approved_cap_usd: float, price_per_mtok: float = 1.04, dry_run: bool = False, error_log_path: str | None = None, max_context_tokens: int = 131072, max_retries: int = 4, _sdk_client=None)` with method `complete(messages, model, temperature, seed, max_tokens, kind="verdict") -> str` and properties `spent_usd: float`, `total_tokens: int`. Thread-safe accounting. `kind` ∈ {"query","oracle","verdict"} selects the dry-run canned response.
- The real SDK path uses `together.Together().chat.completions.create(...)` and reads `response.usage.prompt_tokens/completion_tokens` — only constructed when `_sdk_client is None and not dry_run`, i.e. never in tests.

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_client.py`:
```python
import json

import pytest

from rejudge import api_client as ac

MSGS = [{"role": "user", "content": "hello"}]


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 100


class _Choice:
    class message:
        content = "YES"


class _Resp:
    usage = _Usage()
    choices = [_Choice()]


class StubSDK:
    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                if outer.calls <= outer.fail_times:
                    raise RuntimeError("transient API error")
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_dry_run_is_tagged_and_free():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    out = c.complete(MSGS, "m", 0.1, 1, 64, kind="oracle")
    assert "DRY RUN" in out or out in ("YES", "NO", "NOT ADDRESSED")
    v = c.complete(MSGS, "m", 0.1, 1, 64, kind="verdict")
    assert "DRY RUN" in v and "VERDICT:" in v
    q = c.complete(MSGS, "m", 0.1, 1, 64, kind="query")
    assert q.startswith("CLAIM:")


def test_accounting_and_cap_abort():
    # cap chosen so the SECOND call's projected spend (actual 1100 spent + ~65 estimated)
    # crosses it: (1100+65)/1e6*1.04 ≈ $0.00121 > $0.0012
    c = ac.RejudgeClient(approved_cap_usd=0.0012, _sdk_client=StubSDK())
    c.complete(MSGS, "m", 0.1, 1, 64)          # 1100 tokens -> $0.001144
    assert c.total_tokens == 1100
    assert 0.001 < c.spent_usd < 0.0013
    with pytest.raises(ac.CapExceededError):
        c.complete(MSGS, "m", 0.1, 1, 64)      # projected spend crosses the cap BEFORE calling


def test_retry_then_success(tmp_path):
    log = tmp_path / "errors.jsonl"
    sdk = StubSDK(fail_times=2)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk,
                         error_log_path=str(log), _sleep=lambda s: None)
    assert c.complete(MSGS, "m", 0.1, 1, 64) == "YES"
    assert sdk.calls == 3
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["error"].startswith("transient")


def test_retries_exhausted_raises(tmp_path):
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=2,
                         error_log_path=str(tmp_path / "e.jsonl"), _sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.complete(MSGS, "m", 0.1, 1, 64)


def test_context_guard():
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDK(), max_context_tokens=50)
    big = [{"role": "user", "content": "x" * 4000}]      # ~1000 tokens est.
    with pytest.raises(ac.ContextGuardError):
        c.complete(big, "m", 0.1, 1, 64)
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_client.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/api_client.py`:**
```python
"""Together client wrapper: retries/backoff, cost cap, context guard, dry-run tagging.

The real SDK is imported lazily and only when needed, so tests never touch it.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone


class CapExceededError(RuntimeError):
    pass


class ContextGuardError(RuntimeError):
    pass


_DRY = {
    "query": "CLAIM: [DRY RUN] the sky over the capital is described as blue",
    "oracle": "YES [DRY RUN]",
    "verdict": "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: [DRY RUN] synthetic response.",
}


def _estimate_tokens(messages, max_tokens):
    return sum(len(m["content"]) for m in messages) // 4 + max_tokens


class RejudgeClient:
    def __init__(self, approved_cap_usd, price_per_mtok=1.04, dry_run=False,
                 error_log_path=None, max_context_tokens=131072, max_retries=4,
                 _sdk_client=None, _sleep=time.sleep):
        self.approved_cap_usd = approved_cap_usd
        self.price_per_mtok = price_per_mtok
        self.dry_run = dry_run
        self.error_log_path = error_log_path
        self.max_context_tokens = max_context_tokens
        self.max_retries = max_retries
        self._sdk = _sdk_client
        self._sleep = _sleep
        self._lock = threading.Lock()
        self.total_tokens = 0

    @property
    def spent_usd(self) -> float:
        return self.total_tokens / 1_000_000 * self.price_per_mtok

    def _log_error(self, attempt, model, exc):
        if not self.error_log_path:
            return
        with self._lock:
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                    "attempt": attempt, "model": model,
                                    "error": str(exc)}) + "\n")

    def _client(self):
        if self._sdk is None:
            from together import Together
            self._sdk = Together()
        return self._sdk

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict") -> str:
        est = _estimate_tokens(messages, max_tokens)
        if est > self.max_context_tokens:
            raise ContextGuardError(f"estimated {est} tokens > {self.max_context_tokens}")
        if self.dry_run:
            return _DRY[kind]
        with self._lock:
            projected = (self.total_tokens + est) / 1_000_000 * self.price_per_mtok
            if projected > self.approved_cap_usd:
                raise CapExceededError(
                    f"projected spend ${projected:.4f} > approved cap ${self.approved_cap_usd:.4f}")
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client().chat.completions.create(
                    model=model, messages=messages, temperature=temperature,
                    max_tokens=max_tokens, seed=seed)
                with self._lock:
                    self.total_tokens += (resp.usage.prompt_tokens + resp.usage.completion_tokens)
                content = resp.choices[0].message.content
                return content if content is not None else ""
            except (CapExceededError, ContextGuardError):
                raise
            except Exception as exc:                     # transient API error
                last = exc
                self._log_error(attempt, model, exc)
                if attempt < self.max_retries:
                    self._sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"API call failed after {self.max_retries + 1} attempts: {last}")
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_client.py -q` → pass. Note the cap test asserts the abort happens BEFORE the second call (projected-cost check), matching the implementation.
- [ ] **Step 5: Commit:** `git add rejudge/api_client.py tests/test_rejudge_client.py && git commit -m "Add cost-capped, retrying, context-guarded API client with tagged dry-run"`

### Task 11: Judge loop with per-arm hooks + golden replay tests

**Files:**
- Create: `rejudge/judge_loop.py`
- Test: `tests/test_rejudge_judge_loop.py`

**Interfaces:**
- Consumes: everything from Tasks 5–10 (`config.ARMS/PLACEBO_TEXT/clean_query_phase_prompt/position_for/judgment_seed`, `composer.*`, `oracle_channel.*`, `parsers.parse_both`, `records.build_record`).
- Produces: `run_judgment(transcript: dict, world_document: str, arm: ArmSpec, budget: int, replicate: int, client, protocol: dict, judge_model: str = config.JUDGE_MODEL) -> dict` (a record). `transcript` is a raw dict from `data/transcripts.jsonl`. The judge-messages flow ports `judge.py:run_judgment` exactly, with hooks:
  - transcript formatting ports `judge.py:_format_transcript` (labels turns by `position_a_is_correct` and `turn.speaker`);
  - query phase uses `clean_query_phase_prompt(...)` for clean-composer arms, the pilot template verbatim for pilot-composer arms;
  - after each query response: DONE check per arm; claim extraction per arm; placebo arms skip the oracle call and feed back `PLACEBO_TEXT`; others call the oracle and feed back `f"Oracle result: {normalized}"` (ports pilot feedback format);
  - exchange log entry per query: `{"raw_query_response", "extracted_claim", "well_formed_claim" (clean only, else None), "oracle_prompt": <full literal prompt or None for placebo>, "raw_oracle_reply": <str or None>, "normalized": <str or None>, "placebo": bool}`;
  - verdict phase ports the pilot verbatim (`VERIFICATION RESULTS` block + `verdict_prompt`), then `parse_both`.

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_judge_loop.py`:
```python
import json

from rejudge import config, judge_loop


class ScriptedClient:
    """Returns scripted responses by kind; records every call."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict"):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages]})
        v = self.script[kind]
        return v.pop(0) if isinstance(v, list) else v


def _tr():
    rows = [json.loads(l) for l in open("data/transcripts.jsonl", encoding="utf-8")]
    return rows[0]


def _protocol():
    return config.load_protocol()


JUDGE_Q = "Is it stated in the text that the treaty was signed in Year 38?"


def test_both_arm_reproduces_doubling_and_na_to_no():
    client = ScriptedClient({"query": [JUDGE_Q], "oracle": "NOT ADDRESSED",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["both"], 1, 0,
                                  client, _protocol())
    ex = rec["exchanges"][0]
    assert "Is it supported by the text that Is it stated in the text that" in ex["oracle_prompt"]
    assert ex["raw_oracle_reply"] == "NOT ADDRESSED"
    assert ex["normalized"] == "NO"                     # the NA->NO bug, replayed
    oracle_call = [c for c in client.calls if c["kind"] == "oracle"][0]
    assert ex["oracle_prompt"] == oracle_call["messages"][-1]["content"]  # literal text logged


def test_clean_arm_single_wrap_and_na_preserved():
    client = ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"],
                             "oracle": "NOT ADDRESSED",
                             "verdict": "VERDICT: Position B\nCONFIDENCE: 3\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["clean"], 1, 0,
                                  client, _protocol())
    ex = rec["exchanges"][0]
    assert ex["oracle_prompt"].count("Is it supported by the text that") == 1
    assert ex["normalized"] == "NOT ADDRESSED"
    assert ex["well_formed_claim"] is True


def test_placebo_arm_no_oracle_call_fixed_feedback():
    client = ScriptedClient({"query": ["CLAIM: something"],
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["placebo"], 1, 0,
                                  client, _protocol())
    assert all(c["kind"] != "oracle" for c in client.calls)
    ex = rec["exchanges"][0]
    assert ex["placebo"] is True and ex["oracle_prompt"] is None
    feedback = [m for c in client.calls if c["kind"] == "verdict"
                for m in c["messages"] if m["role"] == "user" and "ORACLE PLACEBO" in m["content"]]
    assert feedback, "placebo text must be fed back to the judge"


def test_done_handling_differs_by_arm():
    protocol = _protocol()
    # robust arm stops on "I'm done."
    c1 = ScriptedClient({"query": ["I'm done."],
                         "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec1 = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 2, 0, c1, protocol)
    assert rec1["queries_used"] == 0
    # pilot arm treats "I'm done." as a query (the pilot brittleness)
    c2 = ScriptedClient({"query": ["I'm done.", "DONE"], "oracle": "YES",
                         "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec2 = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["both"], 2, 0, c2, protocol)
    assert rec2["queries_used"] == 1


def test_ab_fixed_across_budgets_and_replay_stability():
    protocol = _protocol()

    def run(budget):
        c = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES",
                            "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
        return judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], budget, 0, c, protocol)

    r1, r5 = run(1), run(5)
    assert r1["position_a_is_correct"] == r5["position_a_is_correct"]
    # byte-stability modulo created_at
    a, b = run(1), run(1)
    a.pop("created_at"); b.pop("created_at")
    assert a == b
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_judge_loop.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/judge_loop.py`:**
```python
"""The re-judge core loop: a port of the pilot judge flow with per-arm hooks."""
from __future__ import annotations

from rejudge import composer, oracle_channel, records
from rejudge.config import ARMS, JUDGE_MODEL, PLACEBO_TEXT, ArmSpec, clean_query_phase_prompt, \
    judgment_seed, position_for
from rejudge.parsers import parse_both


def _format_transcript(tr: dict, position_a_is_correct: bool):
    """Port of judge.py:_format_transcript for raw-dict transcripts."""
    if position_a_is_correct:
        position_a, position_b = tr["correct_answer"], tr["wrong_answer"]
    else:
        position_a, position_b = tr["wrong_answer"], tr["correct_answer"]
    turns = []
    for i, turn in enumerate(tr["debate_transcript"]):
        if position_a_is_correct:
            label = "Debater A" if turn["speaker"] == "honest" else "Debater B"
        else:
            label = "Debater B" if turn["speaker"] == "honest" else "Debater A"
        turns.append(f"Turn {i + 1} ({label}):\n{turn['text']}")
    return position_a, position_b, "\n\n".join(turns)


def _format_previous(exchange_feedback: list[tuple[str, str]]) -> str:
    if not exchange_feedback:
        return "No queries submitted yet."
    return "\n\n".join(f"Query {i}: {q}\nResult: {r}"
                       for i, (q, r) in enumerate(exchange_feedback, 1))


def run_judgment(transcript: dict, world_document: str, arm: ArmSpec, budget: int,
                 replicate: int, client, protocol: dict,
                 judge_model: str = JUDGE_MODEL) -> dict:
    qid, tidx = transcript["question_id"], transcript["transcript_index"]
    pos_a_correct = position_for(arm, qid, tidx, judge_model, budget)
    seed = judgment_seed(qid, tidx, judge_model, budget, arm.name, replicate)

    judge_cfg = protocol["judge"]
    oracle_cfg = protocol["oracle"]
    oracle_model = protocol["protocol"]["models"]["oracle"]
    t_judge = protocol["protocol"]["temperature"]["judge"]
    t_oracle = protocol["protocol"]["temperature"]["oracle"]

    query_template = (judge_cfg["query_phase_prompt"] if arm.composer == "pilot"
                      else clean_query_phase_prompt(judge_cfg["query_phase_prompt"]))
    is_done = (oracle_channel.is_done_pilot if arm.done_detector == "pilot"
               else oracle_channel.is_done_robust)
    normalize = (oracle_channel.normalize_pilot if arm.oracle_normalizer == "pilot"
                 else oracle_channel.normalize_strict)

    position_a, position_b, debate_text = _format_transcript(transcript, pos_a_correct)
    messages = [
        {"role": "system", "content": judge_cfg["system_prompt"]},
        {"role": "user", "content": judge_cfg["user_prompt_template"].format(
            question=transcript["question"], position_a=position_a,
            position_b=position_b, debate_transcript=debate_text)},
    ]

    exchanges = []
    feedback_pairs = []          # (claim-as-shown, result-as-shown) for the verdict block
    if budget > 0:
        for query_num in range(budget):
            remaining = budget - query_num
            messages.append({"role": "user", "content": query_template.format(
                remaining_budget=remaining, total_budget=budget,
                previous_queries=_format_previous(feedback_pairs))})
            raw_q = client.complete(messages, judge_model, t_judge,
                                    seed + query_num, 256, kind="query")
            messages.append({"role": "assistant", "content": raw_q})
            if is_done(raw_q):
                break
            if arm.composer == "pilot":
                claim, well_formed = composer.pilot_extract_claim(raw_q), None
            else:
                claim, well_formed = composer.clean_extract_claim(raw_q)
            if arm.placebo:
                oracle_prompt = raw_reply = normalized = None
                shown = PLACEBO_TEXT
                messages.append({"role": "user", "content": shown})
            else:
                oracle_prompt = composer.compose_oracle_prompt(
                    oracle_cfg["user_prompt_template"], world_document, claim)
                raw_reply = client.complete(
                    [{"role": "system", "content": oracle_cfg["system_prompt"]},
                     {"role": "user", "content": oracle_prompt}],
                    oracle_model, t_oracle, seed + 10000 + query_num, 32, kind="oracle")
                normalized = normalize(raw_reply)
                shown = f"Oracle result: {normalized}"
                messages.append({"role": "user", "content": shown})
            feedback_pairs.append((claim, shown if arm.placebo else normalized))
            exchanges.append({"raw_query_response": raw_q, "extracted_claim": claim,
                              "well_formed_claim": well_formed, "oracle_prompt": oracle_prompt,
                              "raw_oracle_reply": raw_reply, "normalized": normalized,
                              "placebo": arm.placebo})

    query_results = ""
    if exchanges:
        query_results = "VERIFICATION RESULTS:\n\n" + "\n\n".join(
            f"Query {i}: {e['extracted_claim']}\nResult: "
            f"{PLACEBO_TEXT if e['placebo'] else e['normalized']}"
            for i, e in enumerate(exchanges, 1))
    messages.append({"role": "user",
                     "content": judge_cfg["verdict_prompt"].format(query_results=query_results)})
    raw_verdict = client.complete(messages, judge_model, t_judge, seed + 99999, 512,
                                  kind="verdict")

    return records.build_record(
        transcript=transcript, arm=arm, budget=budget, replicate=replicate,
        position_a_is_correct=pos_a_correct, exchanges=exchanges,
        raw_verdict_text=raw_verdict, parses=parse_both(raw_verdict),
        judge_messages=messages + [{"role": "assistant", "content": raw_verdict}],
        seed=seed, judge_model=judge_model, oracle_model=oracle_model,
        dry_run=getattr(client, "dry_run", False), queries_used=len(exchanges))
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_judge_loop.py -q` → pass. Then the whole suite: `uv run pytest -q` → green.
- [ ] **Step 5: Commit:** `git add rejudge/judge_loop.py tests/test_rejudge_judge_loop.py && git commit -m "Add re-judge core loop with per-arm hooks and golden replay tests"`

### Task 12: Runner CLI with resume + dry-run e2e

**Files:**
- Create: `rejudge/runner.py`
- Test: `tests/test_rejudge_runner.py`

**Interfaces:**
- Consumes: Tasks 8–11.
- Produces: `iter_cells(arm_names: list[str], budgets: dict[str, list[int]], transcripts: list[dict], replicates: int) -> list[dict]` (each: `{"arm", "budget", "transcript", "replicate", "cell_key"}` — legacy gets replicate 0 only); `load_done_keys(out_path) -> set[str]`; `main(argv: list[str] | None = None) -> int`. CLI flags: `--arms clean,both,placebo` (default all six? NO — default `clean,both,placebo,na_only,doubled_only`; legacy only when explicitly listed), `--replicates 2`, `--limit N` (first N transcripts, for canary), `--approved-cap USD` (required unless `--dry-run`), `--dry-run`, `--workers 8`, `--out rejudge/output/records.jsonl`, `--legacy-subset N` (default 100; only used when legacy in arms).

- [ ] **Step 1: Write the failing tests** — `tests/test_rejudge_runner.py`:
```python
import json

from rejudge import runner


def test_iter_cells_counts_and_legacy_k1():
    trs = [{"question_id": f"Q{i}", "transcript_index": i} for i in range(4)]
    cells = runner.iter_cells(["clean", "legacy"], {"clean": [0, 1], "legacy": [1]},
                              trs, replicates=2)
    clean = [c for c in cells if c["arm"] == "clean"]
    legacy = [c for c in cells if c["arm"] == "legacy"]
    assert len(clean) == 4 * 2 * 2            # transcripts x budgets x K
    assert len(legacy) == 4 * 1 * 1           # legacy is K=1
    assert len({c["cell_key"] for c in cells}) == len(cells)


def test_dry_run_e2e_and_resume(tmp_path):
    out = tmp_path / "records.jsonl"
    rc = runner.main(["--arms", "clean,both,placebo", "--replicates", "1",
                      "--limit", "2", "--dry-run", "--workers", "1",
                      "--out", str(out)])
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    # 2 transcripts x (clean 4 budgets + both 3 + placebo 3) x K=1
    assert len(rows) == 2 * (4 + 3 + 3)
    assert all(r["dry_run"] is True for r in rows)
    assert all(r["harness_version"] for r in rows)
    arms = {r["arm"] for r in rows}
    assert arms == {"clean", "both", "placebo"}
    # resume: second run adds nothing
    rc2 = runner.main(["--arms", "clean,both,placebo", "--replicates", "1",
                       "--limit", "2", "--dry-run", "--workers", "1",
                       "--out", str(out)])
    assert rc2 == 0
    rows2 = out.read_text(encoding="utf-8").splitlines()
    assert len(rows2) == len(rows)


def test_live_requires_cap(tmp_path):
    rc = runner.main(["--arms", "clean", "--limit", "1",
                      "--out", str(tmp_path / "r.jsonl")])
    assert rc == 2                              # refused: no --approved-cap and not --dry-run
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_runner.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/runner.py`:**
```python
"""Re-judge runner CLI. Dry-run by default refuses nothing; live runs REQUIRE --approved-cap."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import judge_loop
from rejudge.api_client import RejudgeClient
from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, load_protocol

DEFAULT_ARMS = "clean,both,placebo,na_only,doubled_only"


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def iter_cells(arm_names, budgets, transcripts, replicates, legacy_subset=100):
    cells = []
    for name in arm_names:
        arm_transcripts = transcripts[:legacy_subset] if name == "legacy" else transcripts
        arm_reps = 1 if name == "legacy" else replicates
        for tr in arm_transcripts:
            for b in budgets[name]:
                for k in range(arm_reps):
                    cells.append({"arm": name, "budget": b, "transcript": tr, "replicate": k,
                                  "cell_key": f"{name}|{tr['question_id']}|"
                                              f"{tr['transcript_index']}|{b}|{k}"})
    return cells


def load_done_keys(out_path) -> set:
    p = Path(out_path)
    if not p.exists():
        return set()
    return {json.loads(l)["cell_key"] for l in p.read_text(encoding="utf-8").splitlines() if l}


def _world_documents():
    docs = {}
    for f in Path("world_specs").glob("*.txt"):
        docs[f.stem] = f.read_text(encoding="utf-8")
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=DEFAULT_ARMS)
    ap.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="rejudge/output/records.jsonl")
    ap.add_argument("--legacy-subset", type=int, default=100)
    args = ap.parse_args(argv)

    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD (spend policy).", file=sys.stderr)
        return 2

    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arm_names if a not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}", file=sys.stderr)
        return 2

    transcripts = _load_jsonl("data/transcripts.jsonl")
    if args.limit:
        transcripts = transcripts[:args.limit]
    protocol = load_protocol()
    worlds = _world_documents()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cells = iter_cells(arm_names, DEFAULT_BUDGETS, transcripts, args.replicates,
                       args.legacy_subset)
    done = load_done_keys(out_path)
    todo = [c for c in cells if c["cell_key"] not in done]
    print(f"{len(cells)} cells, {len(done)} done, {len(todo)} to run "
          f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

    client = RejudgeClient(approved_cap_usd=args.approved_cap or 0.0, dry_run=args.dry_run,
                           error_log_path=str(out_path.parent / "errors.jsonl"))
    lock = threading.Lock()

    def run_cell(cell):
        tr = cell["transcript"]
        rec = judge_loop.run_judgment(tr, worlds[tr["world"]], ARMS[cell["arm"]],
                                      cell["budget"], cell["replicate"], client, protocol)
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run_cell, todo))
    print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
- [ ] **Step 4: Run:** `uv run pytest tests/test_rejudge_runner.py -q` → pass; whole suite `uv run pytest -q` → green.
- [ ] **Step 5: Manual smoke (still $0):** `uv run python -m rejudge.runner --arms clean,both,placebo,na_only,doubled_only --replicates 1 --limit 2 --dry-run --workers 2 --out rejudge/output/smoke.jsonl` → prints cell counts, writes records; then delete `rejudge/output/smoke.jsonl`.
- [ ] **Step 6: Commit:** `git add rejudge/runner.py tests/test_rejudge_runner.py && git commit -m "Add resumable re-judge runner CLI with spend-cap refusal and dry-run e2e"`

### Task 13: Frozen pre-run protocol document

**Files:**
- Create: `rejudge/freeze_protocol.py`, `docs/rejudge-protocol.md` (generated)
- Test: `tests/test_rejudge_freeze.py`

**Interfaces:**
- Consumes: `rejudge.config` (ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, PLACEBO_TEXT, JUDGE_MODEL), `rejudge.parsers.PARSER_VERSION`, `rejudge.records.get_git_sha`.
- Produces: `freeze_protocol.render() -> str`; `python -m rejudge.freeze_protocol` writes `docs/rejudge-protocol.md`.

- [ ] **Step 1: Write the failing test** — `tests/test_rejudge_freeze.py`:
```python
from rejudge import freeze_protocol


def test_render_contains_gates_arms_and_provenance():
    text = freeze_protocol.render()
    for needle in [
        "Δfew ≥ 4", "≤ 2", "3.5", "50%",                      # gates
        "clean", "both", "placebo", "na_only", "doubled_only", "legacy",
        "ORACLE PLACEBO: no factual verification was performed",
        "parser_version", "K=2",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ]:
        assert needle in text, needle
```
- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_rejudge_freeze.py -q` — FAIL.
- [ ] **Step 3: Implement `rejudge/freeze_protocol.py`:**
```python
"""Render the frozen pre-run protocol (pre-registration artifact) from live config."""
from __future__ import annotations

from pathlib import Path

from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, JUDGE_MODEL, PLACEBO_TEXT
from rejudge.parsers import PARSER_VERSION
from rejudge.records import get_git_sha


def render() -> str:
    arm_rows = "\n".join(
        f"| {a.name} | {a.oracle_normalizer} | {a.composer} | {a.done_detector} | "
        f"{'yes' if a.placebo else 'no'} | {DEFAULT_BUDGETS[a.name]} | "
        f"{'per-budget (pilot replay)' if a.randomize_ab_per_budget else 'fixed'} |"
        for a in ARMS.values())
    return f"""# Re-Judge Protocol (frozen pre-run)

**Commit:** `{get_git_sha()}` · **parser_version:** `{PARSER_VERSION}` · **Replicates:** K={DEFAULT_REPLICATES} (legacy K=1)
**Judge = Oracle model:** `{JUDGE_MODEL}` · **Transcripts:** the 318 pilot transcripts (`data/transcripts.jsonl`), unchanged.

## Arms

| arm | oracle normalizer | composer | DONE | placebo | budgets | A/B assignment |
|---|---|---|---|---|---|---|
{arm_rows}

Placebo feedback text (verbatim):

> {PLACEBO_TEXT}

## Pre-registered gates (ex-ante: frozen before any clean data exists)

- **Primary (CLEAN):** Δfew = ½[p(1)+p(2)] − p(0) on strict-parsed verdicts (INVALID excluded and
  reported), question-cluster bootstrap (B=10,000, seeded). Δfew ≥ 4 pp with 95% CI excluding 0 →
  **limited-verification harm survives**. Δfew ≤ 2 pp with CI including 0 → **mostly harness
  artifact**. 2–4 pp → indeterminate (escalate replicates to K=3 before re-judging the gate).
- **Attribution:** BOTH−CLEAN ≥ 3.5 pp, or bugs explain > 50% of the original +7.2 pp → the pilot
  headline was mostly harness-induced.
- **Deliberation:** |PLACEBO − CLEAN| ≤ 2 pp (and PLACEBO−p(0) ≥ 4 pp) → the harm is
  deliberation/turn-count, not verification content.
- **Secondary (reported, not gated):** Δrecover5, dual-parse disagreement rate, INVALID rate,
  well_formed_claim rate, single-bug decomposition (NA_ONLY, DOUBLED_ONLY), legacy-vs-pilot
  agreement (QA only).

## Spend control

Live runs require `--approved-cap` (hard abort on projected overrun). Estimated Stage 1
(all five core arms, K=2, plus legacy subset): ~$185–230 at $1.04/M; approved cap to be
recorded here at launch alongside the account price.
"""


def main():
    Path("docs/rejudge-protocol.md").write_text(render(), encoding="utf-8")
    print("Wrote docs/rejudge-protocol.md")


if __name__ == "__main__":
    main()
```
- [ ] **Step 4: Run test, generate, verify:** `uv run pytest tests/test_rejudge_freeze.py -q` → pass; `uv run python -m rejudge.freeze_protocol`; `grep -c "frozen pre-run" docs/rejudge-protocol.md` → 1.
- [ ] **Step 5: Full suite:** `uv run pytest -q` → everything green.
- [ ] **Step 6: Commit:** `git add rejudge/freeze_protocol.py docs/rejudge-protocol.md tests/test_rejudge_freeze.py && git commit -m "Freeze pre-run re-judge protocol with gates, arms, and provenance"`

---

## Self-review notes

- Spec coverage: Workstream A → Tasks 1–3; parser hardening → Task 4; `rejudge/` components → Tasks 5–12; frozen protocol → Task 13. The spec's "controller redeploys the Artifact" is a controller action after Task 2, not a subagent step. ✓
- The `records.build_record` signature in Task 9 matches its call in Task 11 (keyword-only). `parse_both` output feeds both. `iter_cells` legacy K=1 matches config. ✓
- `RejudgeClient.complete(kind=...)` is consumed by `judge_loop` with kinds query/oracle/verdict, matching `_DRY` and the `ScriptedClient` stubs. ✓
- Task 8 Step 4 contains the recovery path if the protocol anchor line differs from the plan's assumption — the anchor MUST be read from the real file, never guessed. ✓
