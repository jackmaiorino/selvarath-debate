# Validation amendment for the expanded benchmark, 2026-10-08

Status: implemented in code. The paid validation launch needs Jack's separate go.

## Context

Expansion authoring closed on 2026-10-07: all 160 worlds admitted, $150.48 spent in
the expansion run, and $172.56 author-stage total. The unpaid validation plan for all
160 worlds has 1,920 candidate questions, 1,842 mechanical passes and 14,832
validation requests, 14,075 of them new.

## Decisions (Jack, decision cards, 2026-10-08 02:03Z)

1. **DeepSeek answer-key checks use 12,000 tokens (v2) on worlds after W008.** In
   the eight-world cohort, 29 of 190 DeepSeek key checks (15%) hit the 4,000-token
   limit and returned no usable answer. Questions, models, answer orders, frontier
   key checks (4,000) and fact checks (2,000) are unchanged. W001 to W008 keep their
   saved 4,000-token requests, so the cohort is a v1 subset and the expansion a v2
   subset; analyses that pool them should report this.
2. **Caps.** Author is trimmed from $648 to $173 (it used $172.56). Validate rises
   from $200 to $893, covering the v2 worst-case whole-stage reservation of $892.70.
   Main becomes $3,634. Canary $60, pilot $650 and the $590 reserve are unchanged;
   the total stays $6,000.

## Code

- `finalphase/authoring.py`: `EXPANSION_DSPRO_KEY_MAX_TOKENS = 12000` and
  `validator_max_tokens()`, which applies it to DeepSeek key checks outside the frozen
  cohort. Request IDs are unchanged.
- `finalphase/cli.py`: amended `STAGE_CAPS`.
- `finalphase/validation.py`: the plan and the qualification store read the validate
  cap from `STAGE_CAPS`. An authorization carrying the exact
  `validation_input_sha256` of the full request set replaces the per-role envelope,
  which was estimated from the first eight worlds before the new texts existed.

## Expected cost

Expected new spend is about $175 to $180 at observed rates (Anthropic about $43,
OpenAI about $57, Together about $75 under v2). The worst case is $892.70 for the
whole stage, including the $8.76 already spent on the cohort.
