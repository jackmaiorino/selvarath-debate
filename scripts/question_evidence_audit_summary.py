"""Validate and summarize two offline source reviews without reading model results."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

ROOT = Path(__file__).resolve().parents[1]
WORLDS = ("carath_norn", "selvarath", "vethun_sarak")
CATEGORIES = ("decisive", "inferential", "underdetermined")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write_report(out, audit, summary, plot, all_reviews):
    c = summary["consensus_counts"]
    n_core = len(summary["core_evidence_candidates"])
    table = ["| Source world | Decisive core | Inferential | Underdetermined | Reviewers differ |", "|---|---:|---:|---:|---:|"]
    for w in WORLDS:
        v = summary["world_consensus_counts"][w]
        table.append(f"| {w.replace('_', ' ').title()} | {v.get('decisive',0)} | {v.get('inferential',0)} | {v.get('underdetermined',0)} | {v.get('disputed',0)} |")
    table.append(f"| **Total** | **{c.get('decisive',0)}** | **{c.get('inferential',0)}** | **{c.get('underdetermined',0)}** | **{c.get('disputed',0)}** |")
    text = f"""# Question and evidence audit

Completed 2026-09-12. This audit covers all **82 questions** used in Phases 3, 4 and 5. Two AI reviewers independently assessed every question from its full source document and both candidate answers, producing **164 source reviews**. No experiment provider calls were made.

**Main finding:** both reviewers classified {c.get('decisive',0)}/82 core answers as source-determined, {c.get('inferential',0)}/82 as requiring additional interpretative assumptions, and {c.get('underdetermined',0)}/82 as underdetermined. They differed on {c.get('disputed',0)}/82 classifications. This is an audit of source sufficiency, not an estimate of how many original answer keys are wrong.

{chr(10).join(table)}

The reviewers agreed on the determinacy category for **{summary['class_agreements']}/82** questions and preferred the original keyed candidate in both reviews for **{summary['both_prefer_original_key']}/82**. Preference and determinacy are different: a candidate can be better supported than a weak alternative without its central comparison being settled by the source. The stricter criterion here is intended to select diagnostic questions with clear truth conditions. It is not a requirement that every legitimate reasoning task be reducible to literal quotation.

**Potential diagnostic panel:** {n_core} questions have a source-determined core and both reviewers prefer the original key: {', '.join(summary['core_evidence_candidates']) or 'none'}. Candidate explanations were reviewed separately. Their flags include interpretative or speculative extensions, including explicitly tentative claims; these require case-by-case assessment and are not all false statements. For example, a correctly qualified possible treaty interpretation need not itself be a defect. The shortlist is provisional and requires a final check of shortened answer options and paired evidence before use. This audit does not release a certified full-text candidate set or establish that any existing query history already contains sufficient evidence.

Three examples show why the distinction matters:

- **CN-010, consequences of replacing fixed-price grain trade with market pricing:** the source states that pricing changed, but gives neither the old price nor the later market price. A lower market price and a higher market price are both compatible with the document and support different economic stories. See [source paragraph 11](../../world_specs/carath_norn.txt#L11).
- **VS-029, favorable versus exploitative grain loans:** amount, rate and repayment are recorded. Alternative credit terms, bargaining alternatives, risk and the criterion for exploitation are not. Perfect verification of the recorded loan facts would still leave the evaluative comparison open. See [source paragraph 11](../../world_specs/vethun_sarak.txt#L11).
- **CN-018, whether the Moot can prevent consolidation:** the source explicitly states that it currently has no such mechanism. This is the kind of compact decisive fact a controlled evidence experiment could use. See [source paragraph 15](../../world_specs/carath_norn.txt#L15).

There are also specific defects beyond interpretative uncertainty. In **VS-003**, the keyed explanation calls a change from 5-of-7 to 5-of-6 a preservation of the proportional threshold; those fractions are different. It also assigns earlier succession success to the seven-member system even though the current Archon began ruling about Year 79, after the unreversed Year 53 merger. This does not establish that the fallback council procedure was used at that accession, but it rules out assuming every prior accession occurred in the seven-member configuration. Separately, the Vethun Sarak source calls the present year 112 and describes the Year 112 Massing as scheduled for next year. These are local candidate/source defects, not demonstrated causes of the model difference. See [the succession rules](../../world_specs/vethun_sarak.txt#L6), [merger history](../../world_specs/vethun_sarak.txt#L11), and [present situation](../../world_specs/vethun_sarak.txt#L15).

**What this changes:** the earlier numerical results still describe choices against the frozen author key. The audit limits their interpretation as measurements of uniquely correct source-grounded answers across the whole panel. It does not show that ambiguity caused the recipient gap, establish that the alternative keys are correct, or reverse any previous experimental result. No old scores or datasets were changed, and no question was selected because a particular model succeeded or failed.

The information limit is straightforward: if two situations satisfy every supplied source fact but require different answers, an oracle restricted to those source facts cannot distinguish them. Additional correct verification cannot supply the missing premise. The judge must retain uncertainty or introduce assumptions. This conditional argument explains why source sufficiency matters; it does not identify either model's internal reasoning or prove that this mechanism produced the measured gap.

**Recommended next step:** use the {n_core}-question shortlist for a small sanity check after reviewing and shortening its candidate answers. It is too narrow to support a broad transfer conclusion. Before another substantial experiment, author new worlds with explicit rules, independently checked answer keys, and balanced candidate length and specificity. Then compare decisive versus incidental evidence on those questions. Retaining multiple transcripts or mirrored sides does not create additional independent question clusters. Reusing this panel is exploratory; a later transfer claim needs new questions and additional model families. An automatically useful evidence selector remains a separate research question. Whether the decisive facts are reachable under the existing query restrictions also requires a separate check; manually supplying decisive excerpts would be a capability diagnostic, not a deployment test of the original querying protocol.

**Method and limits:** packet construction used the exact 82 main IDs, excluded the same 24 calibration questions, and permuted candidate order with a fixed hash seed. Packets omitted gold labels, author rationales, facts-required lists, model identity, traces and outcomes. The two reviewers used separate fresh contexts and did not see each other's judgments. Both are AI reviewers from the same model family, so their errors may be correlated; this is not independent human validation. Candidate wording can reveal the author's intended preference despite label masking: the keyed candidate is longer in {summary['keyed_candidate_longer_count']}/82 cases. Root retained prior aggregate context and saw some key rows during preparation, then checked provenance, exact quotations and concrete source/candidate defects. Disagreements remain visible instead of being forced into consensus. No verdict files were loaded to classify or select questions.

Every review row has exact source quotations, assumptions and a source-compatible alternative when the core is not decisive. The original question-bank canonical hashes match the experiment protocol, source texts match the archived Phase 4B texts, all 82 IDs match the Phase 4/5 panel, and cited quotes are checked verbatim against their recorded line spans. Raw packets, both full reviews and the original key map are kept outside Git at `{audit}`. Public artifacts: [question-by-question matrix](audit-matrix.md), [candidate evidence excerpts](evidence-candidates.md), [full audit summary](summary.json), and [verification](verification.json).
"""
    if plot:
        text += "\n![Source determinacy by world](source-determinacy.png)\n"
    (out/"audit.md").write_text(text, encoding="utf-8", newline="\n")
    matrix = ["# All 82 question reviews", "", "D = source-determined core; I = additional inference needed; U = underdetermined. The two review columns are passes, not stable reviewer identities across worlds. A flag is not necessarily a false statement. Detailed reasons are in summary.json and private review files.", "", "| Question | Review 1 | Review 2 | Both prefer original key | Core diagnostic candidate | Key text flagged by either |", "|---|---|---|---|---|---|"]
    labels = {"decisive":"D", "inferential":"I", "underdetermined":"U"}
    for r in summary["rows"]:
        yn=lambda x: "yes" if x else "no"
        matrix.append(f"| {r['question_id']} | {labels[r['review1_class']]} | {labels[r['review2_class']]} | {yn(r['review1_prefers_key'] and r['review2_prefers_key'])} | {yn(r['core_evidence_candidate'])} | {yn(r['key_text_flagged_by_either'])} |")
    (out/"audit-matrix.md").write_text("\n".join(matrix)+"\n",encoding="utf-8",newline="\n")
    excerpts = ["# Candidate decisive evidence", "", "These are source excerpts supporting the provisional core-answer shortlist, copied from the first review of each world and verified verbatim. They are not finalized experimental packets, repaired answer keys, or evidence that an automatic query policy can obtain the same content. Candidate explanations may require shortening. All items have already been used in the experiment.", ""]
    for row in summary["rows"]:
        if not row["core_evidence_candidate"]:
            continue
        r = all_reviews[row["world"]][0][row["question_id"]]
        excerpts += [f"## {row['question_id']}", "", r["rationale"], ""]
        for e in r["evidence"]:
            excerpts += [f"> {e['quote']}", "", f"[Source line {e['line_start']}](../../world_specs/{row['world']}.txt#L{e['line_start']})", ""]
    (out/"evidence-candidates.md").write_text("\n".join(excerpts),encoding="utf-8",newline="\n")
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        colors = {"decisive":"#287D70", "inferential":"#D89C35", "underdetermined":"#B95759", "disputed":"#8B95A5"}
        names = {"decisive":"Source-determined", "inferential":"Additional inference", "underdetermined":"Underdetermined", "disputed":"Reviewers differ"}
        fig, ax = plt.subplots(figsize=(10, 4.4), dpi=180)
        fig.patch.set_facecolor("#FAFAF8")
        ax.set_facecolor("#FAFAF8")
        for y,w in enumerate(WORLDS):
            left=0
            for cat in (*CATEGORIES,"disputed"):
                value=summary["world_consensus_counts"][w].get(cat,0)
                ax.barh(y,value,left=left,height=.52,color=colors[cat],label=names[cat] if y==0 else None)
                if value:
                    ax.text(left+value/2,y,str(value),ha="center",va="center",color="white",fontweight="bold",fontsize=11)
                left+=value
        ax.set_yticks(range(3),[w.replace('_',' ').title() for w in WORLDS])
        ax.invert_yaxis()
        ax.set_xlim(0,29)
        ax.set_xlabel("Questions, one count per original question")
        ax.set_xticks(range(0,30,5))
        ax.tick_params(axis='both',length=0)
        for spine in ax.spines.values(): spine.set_visible(False)
        ax.legend(loc="upper center",bbox_to_anchor=(.5,-.22),ncol=2,frameon=False)
        fig.suptitle("How much does the source actually determine?",x=.04,ha="left",fontsize=17,fontweight="bold")
        fig.text(.04,.865,"82 questions | two separate AI reviews per question | disagreements retained",fontsize=10,color="#4F5661")
        fig.text(.04,.015,"These are source-review judgments, not model error rates or human-validated truth labels.",fontsize=9,color="#4F5661")
        fig.subplots_adjust(left=.18,right=.97,top=.80,bottom=.32)
        fig.savefig(out/"source-determinacy.png",facecolor=fig.get_facecolor())
        fig.savefig(out/"source-determinacy.svg",facecolor=fig.get_facecolor())
        svg = out/"source-determinacy.svg"
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())+"\n",
                       encoding="utf-8",newline="\n")
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    audit = args.audit.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = read(audit / "manifest.json")
    assert all((audit/f"review{p}_{w}.json").exists() for p in (1,2) for w in WORLDS)
    key = read(audit / "key_private.json")
    source_checks = {p: sha(Path(p)) == expected for p, expected in manifest["source_hashes"].items()}
    assert all(source_checks.values()), source_checks
    assert sha(audit / "key_private.json") == manifest["key_sha256"]
    hashes = {}
    rows = []
    quotes_checked = 0
    all_reviews = {}
    for world in WORLDS:
        packet_path = audit / f"{world}_packet.json"
        assert sha(packet_path) == manifest["packet_hashes"][packet_path.name]
        packet = read(packet_path)
        source = [s["text"] for s in packet["source_lines"]]
        qids = {q["question_id"] for q in packet["questions"]}
        qmap = {q["question_id"]: q for q in packet["questions"]}
        reviews = []
        for pass_num in (1, 2):
            path = audit / f"review{pass_num}_{world}.json"
            review = read(path)
            assert isinstance(review, list)
            assert len(review) == len(qids)
            indexed = {r["question_id"]: r for r in review}
            assert set(indexed) == qids
            hashes[path.name] = sha(path)
            for r in review:
                assert r["core_determinacy"] in CATEGORIES, r["question_id"]
                assert r["preferred_option"] in ("A", "B", "unresolved")
                assert isinstance(r["candidate_issues"]["A"], list)
                assert isinstance(r["candidate_issues"]["B"], list)
                assert isinstance(r["missing_assumptions"], list)
                assert r["rationale"]
                if r["core_determinacy"] != "decisive":
                    assert r["countermodel"], r["question_id"]
                if r["clean_evidence_candidate"]:
                    assert r["core_determinacy"] == "decisive"
                    assert r["preferred_option"] in ("A", "B")
                assert 1 <= len(r["evidence"]) <= 4
                for e in r["evidence"]:
                    start, end = e["line_start"], e["line_end"]
                    assert 1 <= start <= end <= len(source)
                    assert e["quote"] and e["quote"] in "\n".join(source[start-1:end]), (path.name, r["question_id"], e)
                    quotes_checked += 1
            reviews.append(indexed)
        all_reviews[world] = reviews
        for qid in sorted(qids):
            a, b = [r[qid] for r in reviews]
            gold = key[qid]["gold_option"]
            agree_class = a["core_determinacy"] == b["core_determinacy"]
            agree_option = a["preferred_option"] == b["preferred_option"]
            core_candidate = all(r["clean_evidence_candidate"] and r["preferred_option"] == gold for r in (a,b))
            issues = [r["candidate_issues"][gold] for r in (a,b)]
            rows.append({
                "question_id": qid, "world": world,
                "keyed_candidate_longer": len(qmap[qid][gold]) > len(qmap[qid]["B" if gold=="A" else "A"]),
                "review1_class": a["core_determinacy"], "review2_class": b["core_determinacy"],
                "consensus_class": a["core_determinacy"] if agree_class else "disputed",
                "class_agreement": agree_class, "option_agreement": agree_option,
                "review1_prefers_key": a["preferred_option"] == gold,
                "review2_prefers_key": b["preferred_option"] == gold,
                "review1_preference_unresolved": a["preferred_option"] == "unresolved",
                "review2_preference_unresolved": b["preferred_option"] == "unresolved",
                "core_evidence_candidate": core_candidate,
                "existing_key_text_unflagged_candidate": core_candidate and not any(issues),
                "key_text_flagged_by_either": any(issues),
                "key_text_flagged_by_both": all(issues),
                "review1_rationale": a["rationale"], "review2_rationale": b["rationale"],
                "review1_key_text_issues": issues[0], "review2_key_text_issues": issues[1],
                "source_lines": sorted({i for r in (a,b) for e in r["evidence"] for i in range(e["line_start"],e["line_end"]+1)}),
            })
    assert len(rows) == 82 and {r["question_id"] for r in rows} == set(key)
    # Bind the concrete keyed-explanation example used in the narrative.
    assert key["VS-003"]["gold_option"] == "B"
    protocol = read(ROOT / "rejudge/phase2_protocol.json")
    binding_checks = {}
    bank_ids = set()
    for rel in protocol["question_set"]["question_sources"]:
        bank = read(ROOT/rel)
        bank_ids.update(q["id"] for q in bank)
        actual = hashlib.sha256(json.dumps(bank, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        binding_checks[rel] = actual == protocol["source_bindings"]["canonical_json_sha256"][rel]
    assert all(binding_checks.values())
    assert bank_ids - set(protocol["question_set"]["calibration_excluded_question_ids"]) == set(key)
    panel_checks = {}
    for phase in ("phase4", "phase5"):
        path = Path(fr"E:\selvarath-archive\{phase}-preparation-2026-09-12\units_private.jsonl")
        units = [json.loads(line) for line in path.read_text().splitlines()]
        ids = {u["question_id"] for u in units}
        assert ids == set(key) and len(units) == 656
        panel_checks[phase] = {"path": str(path), "sha256": sha(path), "units": len(units), "question_ids_match": True}
    archived_world_path = Path(r"E:\selvarath-archive\phase4b-preparation-2026-09-12\worlds_private.jsonl")
    archived_worlds = [json.loads(line) for line in archived_world_path.read_text().splitlines()]
    world_checks = {w: any((ROOT/"world_specs"/f"{w}.txt").read_text(encoding="utf-8") == d["world_document"] for d in archived_worlds) for w in WORLDS}
    assert all(world_checks.values())
    summary = {
        "audit": "question-evidence-audit-2026-09-12-v1", "status": "two_AI_source_reviews_complete",
        "questions": 82, "review_rows": 164,
        "reviewer_assignment": {"source_audit_cn": ["review1_carath_norn", "review2_selvarath", "review2_vethun_sarak"],
                                "source_audit_sel": ["review2_carath_norn", "review1_selvarath", "review1_vethun_sarak"]},
        "world_counts": dict(Counter(r["world"] for r in rows)),
        "consensus_counts": dict(Counter(r["consensus_class"] for r in rows)),
        "world_consensus_counts": {w: dict(Counter(r["consensus_class"] for r in rows if r["world"]==w)) for w in WORLDS},
        "class_agreements": sum(r["class_agreement"] for r in rows),
        "option_agreements": sum(r["option_agreement"] for r in rows),
        "both_prefer_original_key": sum(r["review1_prefers_key"] and r["review2_prefers_key"] for r in rows),
        "keyed_candidate_longer_count": sum(r["keyed_candidate_longer"] for r in rows),
        "core_evidence_candidates": [r["question_id"] for r in rows if r["core_evidence_candidate"]],
        "existing_key_text_unflagged_candidates": [r["question_id"] for r in rows if r["existing_key_text_unflagged_candidate"]],
        "key_text_flagged_by_either": sum(r["key_text_flagged_by_either"] for r in rows),
        "key_text_flagged_by_both": sum(r["key_text_flagged_by_both"] for r in rows),
        "rows": rows,
        "limitations": ["Two AI reviewers, same model family, separate fresh contexts; not independent human validation.",
                        "Reviewer packets omit key labels, outcomes, traces, model identity and author notes; candidate writing style can reveal intended preference.",
                        "Root has prior aggregate results context and prepared keys; classifications retained separately rather than forcing agreement.",
                        "A source-determined core does not certify every clause of a candidate explanation.",
                        "Flags are review judgments, not measured label-error prevalence; ambiguous does not mean the opposite answer is correct.",
                        "No old outcomes were rescored and no claim is made that ambiguity explains recipient differences.",
                        "Source sufficiency is not coverage of actual captured query histories or proof of automatic evidence selection.",
                        "All questions were previously used; this audit creates no held-out evaluation data."],
        "provider_calls": 0,
    }
    dump(out/"summary.json", summary)
    write_report(out, audit, summary, args.plot, all_reviews)
    verification = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "questions": 82, "review_rows": 164, "quotes_exactly_verified": quotes_checked,
        "source_hashes_unchanged": source_checks,
        "question_bank_protocol_bindings_match": binding_checks,
        "main_bank_ids_after_24_calibration_exclusions_match": True,
        "phase4_phase5_panel_membership": panel_checks,
        "world_texts_match_phase4b_archive": world_checks,
        "review_file_hashes": hashes,
        "manifest_sha256": sha(audit/"manifest.json"),
        "review_instructions_sha256": sha(audit/"review_instructions.md"),
        "summary_script_sha256": sha(Path(__file__)),
        "python": platform.python_version(),
        "rubric_clarification_sha256": sha(audit/"rubric_clarification.md"),
        "root_source_notes_sha256": sha(audit/"root_source_notes.json"),
        "packet_reproduction": read(audit/"packet_reproduction.json"),
        "report_review": read(audit/"report_review.json"),
        "summary_sha256": sha(out/"summary.json"),
        "model_results_files_read": [], "provider_calls": 0,
        "report_hashes": {p.name: sha(p) for p in out.iterdir() if p.name in {"audit.md", "audit-matrix.md", "evidence-candidates.md", "source-determinacy.png", "source-determinacy.svg"}},
    }
    dump(out/"verification.json", verification)
    print(json.dumps({k:v for k,v in summary.items() if k not in ("rows", "limitations")}, indent=2))


if __name__ == "__main__":
    main()
