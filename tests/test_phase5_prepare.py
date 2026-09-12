import copy
from collections import Counter
import json
from pathlib import Path

import pytest
from scripts import phase5_prepare as prep


@pytest.fixture
def panel():
    unit = {"unit_id": "u", "question_id": "q", "world": "w", "debater": "d", "transcript_index": 0, "side": 0}
    messages = [{"role": "system", "content": "Judge café NO carefully."}, {"role": "user", "content": "Debate includes YES and NO."},
                {"role": "user", "content": "Choose a verdict."}]
    def packet(pid, content):
        return {"packet_id": pid, "messages": content, "messages_sha256": prep.message_sha(content)}
    empty = [packet("u:empty", copy.deepcopy(messages))]
    histories = []
    for context in prep.CONTEXTS[1:]:
        history = copy.deepcopy(messages)
        history[2:2] = [{"role":"assistant","content":"Query about " + context},
                        {"role":"user","content":"Result: NOT ADDRESSED\nBlocked query provides no factual evidence."}]
        histories.append(packet("u:" + context + "_repaired", history))
    return [unit], empty, histories, prep.load_protocol()


def test_only_fixed_system_suffix_changes_and_sources_are_immutable(panel):
    before = copy.deepcopy(panel)
    packets, calls, edits = prep.build_panel(*panel)
    assert panel == before
    assert len(packets) == 6 and len(calls) == 12 and len(edits) == 3
    indexed = {p['packet_id']:p for p in packets}
    for context in prep.CONTEXTS:
        ordinary = indexed['u:ordinary_' + context]['messages']
        scoped = indexed['u:scope_' + context]['messages']
        assert scoped[1:] == ordinary[1:]
        assert scoped[0]['content'] == ordinary[0]['content'] + panel[3]['intervention']['scope_suffix']
        assert all(set(m) == {'role','content'} for m in scoped)
    assert Counter(c['arm'] for c in calls) == {arm:2 for arm in prep.ARMS}
    for judge in prep.MODELS:
        assert len({c['seed'] for c in calls if c['judge']==judge}) == 1
        assert all({k:c[k] for k in ('temperature','max_tokens','stream')} == prep.MODEL_SETTINGS[judge]
                   for c in calls if c['judge']==judge)
    assert all(e['message_index']==0 and e['other_messages_unchanged'] for e in edits)


def test_fresh_empty_and_history_pairs_and_seed_namespace(panel):
    packets, calls, _ = prep.build_panel(*panel)
    assert len({c['cell_id'] for c in calls}) == 12
    assert len({c['packet_id'] for c in calls}) == 6
    assert Counter(c['packet_id'] for c in calls) == {p['packet_id']:2 for p in packets}
    assert prep.verdict_seed('u',prep.QWEN) != prep.verdict_seed('u',prep.LLAMA)
    assert prep.build_panel(*panel) == prep.build_panel(*panel)


@pytest.mark.parametrize('corruption', ['missing_empty','missing_history','duplicate_history','wrong_hash','shared_context','saved_verdict','already_scoped'])
def test_source_errors_prevent_construction(panel,corruption):
    units,empty,history,protocol=panel
    if corruption=='missing_empty': empty.clear()
    elif corruption=='missing_history': history.pop()
    elif corruption=='duplicate_history': history.append(copy.deepcopy(history[0]))
    elif corruption=='wrong_hash': history[0]['messages_sha256']='0'*64
    else:
        if corruption=='shared_context': history[0]['messages'][1]['content']+=' changed'
        elif corruption=='saved_verdict': history[0]['messages'].append({'role':'assistant','content':'VERDICT: A'})
        elif corruption=='already_scoped':
            for p in [*empty,*history]:
                p['messages'][0]['content']+=protocol['intervention']['scope_suffix']
                p['messages_sha256']=prep.message_sha(p['messages'])
        history[0]['messages_sha256']=prep.message_sha(history[0]['messages'])
    with pytest.raises(prep.PreparationError): prep.build_panel(*panel)


def test_protocol_edits_rejected_before_sources(tmp_path,monkeypatch):
    p=tmp_path/'protocol.json'; p.write_text('{}')
    monkeypatch.setattr(prep,'PROTOCOL_PATH',p)
    with pytest.raises(prep.PreparationError,match='protocol differs'): prep.load_protocol()


def test_real_frozen_panel_preserves_keys_and_balances_all_cells():
    original=Path(r'E:\selvarath-archive\phase4-preparation-2026-09-12')
    reviewed=Path(r'E:\selvarath-archive\phase4b-recipient-preparation-2026-09-12')
    if not original.exists() or not reviewed.exists(): pytest.skip('Local frozen archives unavailable')
    artifacts,m=prep.prepare(original,reviewed)
    assert artifacts['units_private.jsonl']==(original/'units_private.jsonl').read_bytes()
    calls=[json.loads(s) for s in artifacts['calls.jsonl'].splitlines()]
    packets=[json.loads(s) for s in artifacts['packets.jsonl'].splitlines()]
    edits=[json.loads(s) for s in artifacts['prompt_edits_private.jsonl'].splitlines()]
    assert len(calls)==7872 and len(packets)==3936 and len(edits)==1968
    assert m['paid_execution_authorized'] is False
    assert m['repair_coverage']['blocked_exchanges_preserved']==1704
    assert m['scope_suffix_sha256']==prep.digest(m['scope_suffix'].encode())
    assert len({c['unit_id'] for c in calls})==656
    assert Counter(c['arm'] for c in calls)=={a:1312 for a in prep.ARMS}
    groups={}
    for c in calls: groups.setdefault((c['unit_id'],c['judge']),set()).add(c['seed'])
    assert len(groups)==1312 and all(len(seeds)==1 for seeds in groups.values())
    assert all('top_p' not in c and 'reasoning_effort' not in c for c in calls)
