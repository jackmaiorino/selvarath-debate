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

## Coordinator audit and follow-up reviewer, 2026-10-09

The fresh audit ran in the original coordinating session (Jack's choice): 100
questions (87 random retained, 8 split-validator, W005 carryover). All sampled keys
held; all 8 split rejections were confirmed; W005-Q11 remains sensitivity-only. The
reviewer named a new source-defect world, W123 (its WY 210 quota of 16,000 does not
follow from the one-third rule applied to 52,000), which requires review of all 12
retained W123 questions.

That session reached about 960k of its 1M-token context, so Jack chose a fresh
independent Claude session for the 11 remaining W123 questions. `audit_reasons` now
accepts a question review whose `reviewer_session_id` names a recorded
`follow_up_reviewers` entry with `implementer_review: false`, only for extra
questions of a named defect world. Sampled and split questions must still come from
the coordinating session. W123 then needs Jack's disposition before canary.
