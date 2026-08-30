# Phase-3 main-protocol pre-review

Date: 2026-08-29. Status: offline continuation review, not final methods ratification. This document authorizes no external execution, provider call, or spend.

Current-state update, 2026-08-30: commits `d9d6db6` and `8299c52` implement the blocked
production driver, finalization, provider and reviewer provenance, output completion, and
analysis handoff that were open below. See `reports/2026-08-30-phase3-main-offline-closure.md`
for the remaining gates. The findings below preserve the 2026-08-29 review state.

## Decision

Do not freeze a live main-run manifest or execution identity, and do not request the proposed $875 authorization yet. The canary supports a conditional funding ask for the two named judges. This continuation fixed the journal's unsafe timeout and open-reservation assumptions. Jack confirmed the scientific scope and approved replacing the obsolete eight-block rule with a representative review-capacity preflight in `rejudge/phase3_main_scope_capacity_decision_2026-08-29.json`. The offline capacity plan, materializer, and validator, the fake-only main-runner safety foundation, and the frozen analysis pins and reporting engine now exist. No capacity measurement, live production driver, main outcome, or main analysis report exists.

## Owner-confirmed science

- Named endpoints: `Qwen/Qwen3.8-2.4T-A95B` and `meta-llama/Llama-3.3-70B-Instruct-Turbo`. Claims stay endpoint-specific and do not generalize to model scale or a broader judge population.
- Inventory: 82 questions, five conditions (`b0`, `b1`, `b2`, `b4`, `b8`), 984 slots per judge per condition, 9,840 judgment slots total.
- Primary contrasts D1, D2, D4, and D8 use common question-cluster bootstrap draws and Holm adjustment. S1 remains the separate b8-versus-b2 contrast.
- The tolerant parser is syntax-only. Strict INVALID remains wrong in the primary analysis. Question pre-screening remains prohibited.
- The request journal records every returned response, including visible empties, before downstream processing. The paid four-cell validation supports returned-response journaling and deterministic replay of completed calls at controlled call boundaries. It need not be repeated solely for the journal primitive. It did not test a real process death, the later hardening revision, the production main driver, or the one-seed output-store harness.

## Process reset for the main run

- Use one small manifest containing the Git commit, Python and dependency versions, linker or `not_applicable`, seeds, input and output SHA-256s, and GPU ordinal `not_applicable`.
- Checkout, build, dependency, archive, and separately authorized provider-connectivity checks are retryable. Formal main measurement is single-shot. The capacity measurement has its own frozen two-attempt contract. Cohort 2 is available only after a locally recorded, receipt-bound `attempt_interrupted` event and an operator attestation classifying cohort 1's interruption as environmental. The attestation does not independently prove the cause. Within this trusted-local contract, a completed failure is terminal.
- Any environmental or process interruption voids the formal main identity. A restart requires a fresh manifest, run ID, artifact root, empty stores, and separate exact owner authorization. No measurement row is resumed or carried forward.
- Harness verification is one end-to-end seed rerun with a bit-identical raw output-store hash.
- Keep completed canary and validation archives read-only.

## Findings and continuation work

1. The journal validation exercised a safe call-boundary restart, not a real process death during dispatch. `KillSwitchClient` stops before the next provider call, after all prior responses have already reached the journal. This proves deterministic replay of completed calls, but it does not exercise the in-flight or ledger-settle-to-journal crash windows.

2. The prior `find_ambiguous_dispatches()` detected only a ledger `success` without a journal row and deliberately ignored an unmatched `reserved` event. The paid client writes `reserved` before dispatch. A real process death can therefore leave an open reservation after the request reached the provider, so the previous redispatch assumption was unsafe.

3. The journal layer now binds journalable ledger events to the exact request fingerprint and rejects or reports every unsafe surviving lifecycle state: `unknown_charge`, `charged_malformed`, unmatched `reserved`, success without journal bytes, duplicate success, and missing or mismatched fingerprint binding. Unkeyed events fail unless their attempt IDs are explicitly allowlisted for a separate preflight ledger. A durable execution-identity marker is fsynced before every provider-capable call and removed only after the response journal append is fsynced; any surviving marker blocks every wrapper on that path. A path-level operating-system lock spans refresh, dispatch, and append, so separately opened wrappers cannot sample concurrently. Journal loading now fails on schema/type violations, sequence gaps, duplicate keys, foreign identity, tampering, blank rows, and torn JSON. These protections govern compliant journal wrappers inside trusted runner code. They are not a security sandbox against arbitrary Python, monkeypatching, or direct filesystem mutation, which the live production driver must exclude from its execution boundary.

4. The ratified process reset removes the need for cell-level recovery from those crash windows. `rejudge/phase3_main_runner.py` now provides a fake-only foundation that internally derives the exact 492-transcript plus 9,840-judgment inventory, enforces lease and start-evidence ordering, validates a genesis-only ledger, reconciles the journal, and refuses resume. It exposes no live CLI or provider factory. Its manifest digest is only syntax-checked, its callback and module globals are trusted Python, and its start evidence is not tamper-proof after return. A production driver, clean-completion archive, or external identity registry is still required.

5. The sealed final canary cannot satisfy the old eight-block pace statistic retrospectively. Its reviewer index contains 576 unique rulings over 3.476 hours, which yields only three full one-hour blocks with counts `192, 128, 215`. The old eight-block L90 is therefore undefined. A three-block diagnostic under the same formula is 2,455.84 rulings per 24 elapsed hours, but it is not the declared gate. The replacement plan freezes two disjoint byte-new cohorts of 180 packets from 368 eligible candidate-order swaps, leaving eight unused. Each cohort contains three waves of 60. Completing every valid ruling within 3,600 seconds per wave and 10,800 seconds total certifies 1,440 rulings per 24 hours against the required 1,312. Cohort 1 runs first; cohort 2 is the sole retry under the receipt-bound, operator-attested interruption rule above. Within the validated local history, a completed failure is terminal. The history, interruption evidence, and retained receipts fail closed on missing or inconsistent artifacts, but they are not tamper-proof against coordinated replacement by an actor with trusted-filesystem access. The offline plan, materializer, and validator exist, but no capacity execution or evidence is authorized or complete.

6. The active ask package previously requested an already-completed $2 validation and carried impossible hand-entered timestamps. It now records the completed result, accurately describes the call-boundary validation, and points chronology to the session event, Git commit, and result timestamp without modifying historical records.

7. The analysis pins freeze the exact endpoint roster, 82-question domain, common 10,000-draw world-stratified bootstrap, Holm family, S1, descriptive sup-t band, strict INVALID policy, all-INVALID scenarios, frozen capability anchors, endpoint-specific reporting, and raw integrity hashes. Synthetic and adjacent tests pass. The analysis CLI refuses any nonempty bare terminal or context-exclusion list. A production finalization artifact must validate those records, bounds, hashes, and the exact partition before confirmatory analysis can run.

## Acceptance gates before any main spend request

Completed offline:

- The exact two-endpoint scope and narrow claim language are owner-confirmed.
- The two-cohort capacity contract is frozen and validates against the sealed source.
- Journal hardening and the fake-only runner safety foundation have focused tests.
- Analysis pins, synthetic analysis, and endpoint-specific reporting rules are frozen offline.

Still blocking:

- Separately authorized capacity measurement must pass and remain valid for the bound reviewer CLI, version, host, model, concurrency, and 24-hour evidence window.
- A production driver must validate the small manifest and derive its digest internally before any live factory is constructed. It must bind the exact owner authorization and refreshed dollar cap, and hold one `RunLease` before every mutable artifact is opened.
- The driver must materialize and validate all 492 frozen transcripts, regenerate and bind the context blocklist, use provider worker concurrency 1, and partition all 9,840 planned judgment slots exactly once.
- All provider work must pass through one journaling client with `halt_on_unknown_charge`, full pre-dispatch and final ledger reconciliation, no caller-supplied bypass, and no cross-process measurement resume.
- Production execution must enforce at most 20 terminal judgment cells, at most 4 percent affected mirror units, and the frozen judge-or-condition concentration rule. It must emit the checker-truncation diagnostic.
- A production finalization artifact must admit any terminal or context-ineligible keys by evidence, bounds, hashes, and exact partition before confirmatory analysis.
- Clean completion, archive sealing, persistent identity-reuse prevention, and final output hashes remain unimplemented.
- The one-seed end-to-end rerun must produce a bit-identical raw output-store hash.
- The frozen analysis engine must run on the accepted final store and produce the endpoint-specific and pooled report. Current analysis evidence is synthetic and offline only.
- The certified cost forecast, fresh serverless price snapshot, Together reconciliation, and final methods review must complete.
- Final owner authorization must bind the exact manifest and refreshed dollar cap. A voided identity requires new authorization.
