# Prospective authoring length amendment, 2026-10-05

Jack authorized this authoring-only amendment in the assigned goal on October 5,
before any corrected requests. The existing 1,000 to 1,500 word requirement is
unchanged. Fable's original four worlds had 1,628 to 1,782 words. All original
responses, benchmark files, hashes and spending entries are retained.

Revision `length-v2` appends an explicit whitespace word-count check to Fable's
original authoring prompt: count only `world_text`, including section headings;
plan approximately 1,200 to 1,350 words across the six sections; revise the model's
own draft until it meets the existing bounds; preserve all twelve questions,
required fields and six task types. The full appended text is
`AUTHOR_LENGTH_AMENDMENT` in `finalphase/prompts_authoring.py`. Astra's request
content and artifacts are unchanged. Fable stays at high effort and 64,000 output
tokens. Historical `v1` request construction remains available for offline replay.

The supported authoring command admits only schema-complete worlds in the required
word range. An invalid saved world raises an explicit error; `--replace-invalid`
preserves its bytes before model generation, writes a versioned corrected artifact
and promotes it only after admission. Candidate answers and prose are never edited
by the coordinating agent. Mechanical question checks remain separate from
independent answer-key validation and their failures are reported without repair.

The finite correction helper uses `python -m finalphase.cli author` for every
generation. It records the amendment, snapshots the original benchmark and ledger,
reconciles usage against reported funding, and reserves maximum batch request costs
before dispatch. One corrected W001 generation must comply before W003/W005/W007
are requested. Each has one paid attempt, with no automatic live fallback. Any
additional paid retry requires a fresh reservation within the cumulative $21.75
authoring cap. The prior $13.743585 is included. No stage allocation changes.

Scientific conditions, model assignments, retention rules, seeds, measurement
rules and remaining launch gates are unchanged. Broader authoring, independent
paid validation, canary, pilot and main remain outside this correction's execution
scope. This supersedes the authoring-prompt freeze only for the authorized
prospective Fable length enforcement.
