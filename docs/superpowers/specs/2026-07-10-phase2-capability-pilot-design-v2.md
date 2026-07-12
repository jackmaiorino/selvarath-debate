# Phase 2 v2: Capability Pilot (Revised for Lead Feedback)

**Date:** 2026-07-10 · **Status:** DRAFT v2, supersedes the 2026-07-09 spec · **Inputs:** lead (J. Marcellino) review of Stage-1, Codex consults #09, #10 (gpt-5.5), #10b (gpt-5.6-sol)

## What changed from v1 and why

1. **All debates get regenerated.** The lead disclosed a third pilot implementation issue: debaters
   saw the opponent's upcoming case in advance, so even first-round arguments contain rebuttals.
   Reusing the 318 transcripts while generating blind 405B debates would confound debater capability
   with generation protocol. New debates: blind, counterbalanced speaking order, both capability
   levels, same transcripts-per-question. The 318 become a legacy bridge (one matched old-vs-new 70B
   anchor cell quantifies the protocol shift; never pooled into the grid).
2. **The task gets harder first (power).** Stage-1 clean base rate (1.26% at b0) leaves too little
   headroom for capability slopes, and smarter judges shrink it further. A calibration probe picks
   a hardened protocol BEFORE the grid.
3. **A fourth condition: batch-same-Q&A** (lead's fresh-context idea). Replay the judge's own
   Q&A as a flattened neutral evidence table into a fresh context. Since the chat API is stateless,
   this isolates representation/commitment effects (role labels, self-commitment, framing), not
   "accumulation". Order-shuffled sensitivity included.
4. **Primary estimand reverts to total harm H** (v1 had made content harm V primary). Stage 1 did
   not separately establish V, so elevating it would overclaim. T, V, P are pre-specified
   mechanistic decomposition.

## Estimands (per judge x debater cell)

```
H = p(clean sequential b2) - p(b0)      total limited-verification harm   <- primary slope vs capability
T = p(placebo b2)          - p(b0)      turn-structure harm
V = p(clean b2)            - p(placebo b2)   content harm beyond turns
P = p(clean sequential b2) - p(batch same-Q&A b2)   representation/commitment harm
```

## Staged plan, gates, and costs

| Stage | What | Gate / rule | Est. |
|---|---|---|---|
| 0 | **Outcome-transition audit** of Stage-1: correct→wrong AND wrong→correct AND matched non-flips, clean + placebo arms (17 + 6 majority regressions already extracted) | enrichment claims require the non-flip base; taxonomy re-derived (the NA→NO "oracle pedantry" class cannot exist in clean data) | $0-150 |
| 1 | **Batch-representation replay** on Stage-1 logged exchanges (no new oracle calls; 1 judge call/cell) + order-shuffle sensitivity | informs P before the grid | $50-150 |
| 2 | **Hardening calibration** on 24-30 held-out questions x 2 fresh blind transcripts: (a) blind multi-round baseline, (b) bandwidth-capped multi-round, (c) capped single-round | **selection rule pre-declared, on difficulty and quality only:** 70B b0 error 8-15%, weak judge <35-40%, strongest judge >2-3%, INVALID <2%, honest-side argument coverage and factual density not crippled. NEVER select on largest b2-b0. Preference order: capped multi-round (preserves rebuttal) > single-round (stress-test candidate) | $300-700 |
| 3 | **Regenerate all debates** under the chosen protocol: blind, counterbalanced speaking order, 70B + 405B, 3 transcripts/question both levels | frozen protocol doc before generation | $500-1,500 |
| 4 | **Reduced grid:** 3 judges (low long-context, 70B, 405B) x 2 debaters, fully crossed; conditions b0, clean sequential b2, placebo b2; K=2; batch-same-Q&A along the 70B-debater row; full-document ceiling anchors at 70B/70B and top/top | per-judge smoke gates (INVALID <2%, side-bias <~10pp); capability = frozen solo-QA scores; H slope primary, cluster bootstrap | $2,000-4,000 |
| 5 | Conditional: budget ladder {2,5,10} at anchor cells, analyzing ACTUAL queries used (Stage-1 judges self-limit: mean 2.66 queries even at b5, so nominal budget is not a dose) | only if the grid shows interpretable harm | deferred |
| 6 | Phase 2.5: dishonest-strategy variants (choose-own-argument; explicit permission to lie), one capability level, **assigned wrong answer held fixed initially** (answer selection is otherwise a confound) | after the grid | $300-800 |

**Core envelope: $3,000-6,000**, preserving the remainder of the ~$9.8k for new-world confirmation.
Full-document anchors are labeled a gold-context ceiling, not "infinite oracle" (changes retrieval
and evidence presentation, not just budget).

## Additions from the leads' sync (2026-07-12, Codex #11)

**1. Hardening is word-limit-first, following Khan et al. (arXiv:2402.06782).** Their protocol:
exactly 3 rounds x 150 words per debater turn (900-word transcripts), judge never sees the passage.
Their LLM judges fail 24% in debate, 46% in consultancy, so our 1.3% baseline confirms our task is
far too easy. Calibration probe (replaces the earlier 3-protocol version): 24 questions stratified
by world AND oracle-resolvability class, BOTH debater models (a word cap could erase the very
debater-strength differences we study), two protocols: blind uncapped 3-round vs blind 150-word
3-round. All 3 judges at no-oracle baseline, K=2; capped 2-round as fallback, single-round last
resort. Selection on pre-declared difficulty/formatting/capability-separation criteria, never on
the size of the oracle effect. Per-judge targets: 70B judge 8-15% wrong (up to 18% ok), weak judge
20-35%, strong judge 3-10%. Word-limit compliance is checked mechanically; violations regenerate.

**2. New reference condition: no-debate (judge + oracle only).** The judge sees the question and
the two candidate answers, no transcript, same oracle interface, budgets 0 and 2 plus the fake
oracle at 2, K=3, per judge (there is no debater dimension). This measures how much debate actually
adds (the Khan-style protocol comparison the leads asked for) as a named secondary quantity:
D = p(wrong with debate) minus p(wrong without debate). Not the same as consultancy (one advocate's
selected evidence), which is noted for phase 3.

**3. The "just ask the oracle" problem, measured and handled.** Audit of all 106 questions
(2026-07-12, $0.36): asking the oracle directly about both candidate answers fully resolves only
6% of questions, partially resolves 39%, and cannot resolve 56%. Important wrinkle: for 15
questions the oracle AFFIRMS the wrong answer (they are defensible by design), so the shortcut
strategy is actively unsafe, which is itself worth reporting. Stage-1 judges spontaneously asked
candidate-answer-level queries in only 3 of 7,627 logged queries. Handling: (a) freeze an oracle
query contract rejecting answer-label, meta-level, and compound queries; (b) STRATIFY results by
resolvability class rather than filtering (directly-resolvable questions are exactly where debate
should add least, so removing them would bias the comparison); (c) verified-quote interfaces (Khan)
noted as the principled long-term enforcement, phase 3.

**4. Out of scope by agreement:** Bayesian-network world modeling.

**5. Batch condition promoted to core (2026-07-12, after the fresh-context replay).** The replay
showed the interactive format itself contributes a large share of the harm (memo:
`reports/2026-07-12-mechanism-and-packaging-memo.md`): identical Q&A as a neutral list cut errors
from 6.09% to 3.14% at budget 2. Therefore batch-same-Q&A runs in EVERY judge x debater cell (one
extra verdict call per judgment), with an empty-evidence-table control at the 70B anchor cell.
Pre-specified capability questions now cover both the sequential harm and the packaging gap P;
residual content harm (batch vs no-oracle) is the third named quantity. Sequential placebo stays
in every cell. Mechanism note for the grid: clean-arm flips split roughly half oracle-model errors
(false NO on claims needing small inference steps), half judge over-reading of true answers
(two blind passes, kappa 0.68); malformed queries are gone.

**Amended stage list:** (0) freeze oracle query contract + finish shortcut audit with human pass,
$0-150; (1) balanced Stage-1 mechanism audit + batch replay, $50-300; (2) two-protocol two-debater
calibration, $300-900; (3) freeze hardened protocol + stratification rules; (4) regenerate all
blind debates, $500-1,500; (5) no-debate reference runs, $100-400; (6) main 3x2 grid, $2,000-4,000;
(7) batch condition on 70B-debater row + full-document ceiling anchors, $200-800; (8) human-in-loop
mechanism labeling on a stratified sample, $300-700; (9) conditional: dishonest strategies and
higher-budget anchors, $600-1,500. Core envelope $3,500-6,500.

## Carried from v1 (unchanged)

Judges: low long-context model (candidate Qwen2.5-7B-Turbo, fallback 14B; Llama-3-8B excluded for
side bias + 8k context), Llama-3.3-70B anchor, Llama-3.1-405B. Oracle pinned at 70B clean oracle in
all cells. Honest = dishonest model per transcript. Model IDs/context/prices verified live before
launch; phase-2 protocol frozen pre-run; capability axis = task-specific solo-QA with full document,
frozen before outcome analysis.
