# Exact unpaid Claude coordinator handoff

Recipient: original session `cd916c75-3534-4496-8864-d4b4f53f3c36` in Claude's
FailureModeExperiment project. Paste the following into that existing session
through the included Claude Max subscription allowance. Current account
configuration reports extra usage disabled at the organization level. The actual
resume failed with `OAuth session expired and could not be refreshed`, with zero
input/output tokens. There is no completed independent review. Do not substitute
a new Codex reviewer or a paid API call. No worktree mutation or launch is requested.

Jack must refresh the subscription login with `claude auth login`; Codex cannot
complete that interactive account authentication. Then, from
`C:/Users/Jack/Dev/FailureModeExperiment`, run the prepared guarded handoff:

```powershell
& D:/finalphase-runs/final-phase-2026-10-03/coordinator_audit_resume.ps1
```

The script clears API/third-party routing only in its process, verifies the Max
subscription and disabled extra usage, resumes the exact original session, and
disables tools, hooks, skills and MCP servers. It preserves each response under a
fresh timestamp. Its complete UTF-8 prompt is `coordinator_audit_prompt.txt`.
Do not use `--bare`: that flag disables subscription authentication. Do not use
a positive API budget to work around authentication. Official [Claude CLI
documentation](https://code.claude.com/docs/en/headless) explains both behaviors
and why resumed `total_cost_usd` includes earlier session turns; the failed
attempt's zero-token usage is the evidence for no new inference.

> Jack requests the unpaid independent source audit required by
> `reports/final-phase-2026-09-30/design.md:55`. Use the original packet
> `D:/finalphase-runs/final-phase-2026-10-03/validation_audit_packet.json` and its
> exact world hashes. Review W006-Q11, W006-Q06, W005-Q11 and W004-Q04 against
> their entire world texts. There are no split-validator questions. Full world
> source input is in `coordinator_audit_input.json`; raw validator responses and
> usage are in `validate.db`. Independently assess each key, every required fact,
> deciding passages, question/rubric criterion agreement, meaningful alternative
> readings and contradictions. Record quotes and a conclusion for each question.
> A source defect in a retained question requires every retained question in that
> world to be reviewed using `bench/validation.jsonl`, with a coordinator
> disposition. Do not edit, revalidate, drop or replenish original questions.
> Preserve 96 candidates, 95 mechanical passes, 72 retained, 23 validation
> rejections and W001-Q01's mechanical rejection. No paid inference, additional
> authoring, validation, canary, pilot or main is authorized. Return the review
> text in this session. Do not modify Claude's worktree. Codex will save the actual
> returned review and link its original session provenance in the existing
> manifest and PR #1. Codex's `implementer_source_review.json` is labelled as
> preparation and cannot satisfy this gate.

Save the actual review as private `coordinator_audit_review.json` with:

```json
{
  "reviewer": "Claude coordinating session",
  "session_id": "cd916c75-3534-4496-8864-d4b4f53f3c36",
  "implementer_review": false,
  "audit_complete": false,
  "packet_sha256": "SHA256 of original validation_audit_packet.json",
  "validation_rows_sha256": "f9da114c47e3d0af6b80bfaa2cb512ef5bbca5ce1c61b9a21a8f598aaf63f77a",
  "retained_source_defect_worlds": [],
  "questions": [
    {"question_id": "W006-Q11", "evidence": [], "conclusion": "actual reviewer conclusion"}
  ]
}
```

The example is incomplete and must not be represented as a review. Completion
requires the actual four reviews plus all triggered world reviews, evidence and
conclusions, and an explicit disposition of any retained source defect. Keep the
original packet unchanged and use the separate actual review as the receipt.
Next wake condition: Jack refreshes the subscription login, then the original
session returns that evidence. The expired-authentication response and zero usage
are preserved in `coordinator_audit_max_response.json` and
`coordinator_audit_attempt_receipt.json`. There is no scheduled watcher or
automatic paid fallback.
