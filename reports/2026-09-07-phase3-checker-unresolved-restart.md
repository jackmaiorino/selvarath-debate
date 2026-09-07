# Phase 3 restart: bounded unresolved checker dispositions

Recorded prospectively on 2026-09-07 before successor execution. Jack requested:
"Ok get it running again and set a monitor so this doesn’t silently happen again".
This is an in-phase recovery under that instruction and the existing Phase 3 delegation,
within the unchanged USD 1,100 aggregate cap.

The stopped run `phase3-main-afc24ecd607773c0` ended at 2026-09-07T11:09:59Z because the
query checker returned the valid label `unresolved`. That label halts the query gate;
the main driver previously admitted only malformed checker output as a terminal INVALID.
The saved response would reproduce the halt on replay. No live run is resumed.

The successor admits both mechanically proved `checker_malformed` and `checker_unresolved`
as terminal INVALID cells. The query gate, checker prompt/model, and request journal remain
unchanged. A disposition must match the successful ledger event, exact request fingerprint,
journal response, and declared reason. Valid allow/reject responses and transport failures
cannot enter this path. Both reasons share the existing maximum of 20 terminal judgment
cells and existing concentration gates. Terminal INVALIDs count as wrong in primary
analysis; existing mirror handling and invalid sensitivities remain in force. Diagnostics
report the two reasons separately. This changes disposition policy, not just validation.

The new identity starts from the frozen transcript inputs with the same models, seeds,
query budgets, analysis pins, and analysis gates. Both stopped identities' outputs remain
immutable operational evidence and are excluded from successor inference. A journal tail
used for failure diagnosis incidentally displayed one preceding judge verdict. No outcome
comparison or selection used it; models, prompts, examples, and scientific gates are fixed.

Stage liabilities are USD 119.27238490 reconciled, USD 24.89969640 from the first stopped
run, and USD 36.15647432 from the second (36.07555192 successful plus 0.08092240 uncertain).
The successor ceiling is therefore USD 919.67144438. The existing USD 908.82 forecast fits
with USD 10.85144438 headroom, subject to a fresh price capture and forecast validation.
The provider client must enforce the combined liability, including uncertain charges.

Existing capacity v7 evidence is reused only while its three bound execution files remain
byte-identical. A focused terminal/replay/accounting test run and the existing deterministic
end-to-end harness precede a clean-commit manifest and delegated signature. The signature
records delegated approval and does not assert Jack personally signed the artifact.

Focused offline validation: 258 tests passed and 2 skipped across terminal handling,
replay, accounting, manifest, and launch materialization. Final harness and launch results
are recorded outside the checkout against the committed source identity.

A Codex heartbeat checks process identity, progress timestamps, stderr, and spend every
10 minutes. It alerts on unexpected exit or a 20-minute progress gap outside a recorded
cooldown, and on completion. The live pointer and launch/monitor records are stored outside
the source checkout under `E:/selvarath-archive`; an active marker alone is never sufficient.
