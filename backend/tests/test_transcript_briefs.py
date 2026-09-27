import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api.document_intelligence import create_document_router
from backend.app.config import Settings
from backend.app.research.document_intelligence import AnalysisStore, analyze_transcript
from backend.app.research.transcript_briefs import TranscriptBriefs, discussion_inputs, validate_discussions, SCHEMA, PROMPT_VERSION
from backend.app.providers.codex import normalize_output_schema

TEXT = '''Jane Doe — CEO
Revenue grew, but costs grew faster and profit fell.
Questions and Answers
Alice Smith — Analyst
I wonder whether costs will keep rising. Will you cut spending?
Jane Doe
Only if sales fall would we cut spending. That is not our base case.
John Smith — CFO
We expect costs to grow more slowly, but we cannot promise that.
'''


def response_for(groups):
    rows = []
    for group in groups:
        bullets = []
        for turn in group['turns']:
            kind = 'question' if turn['id'] in group['question_turn_ids'] else 'answer' if turn['id'] in group['answer_turn_ids'] else 'remark'
            bullets.append({'kind': kind, 'summary': ('They ask whether costs will keep rising and whether spending will be cut.' if kind == 'question' else 'Managers expect costs to grow more slowly, but cannot promise it.' if turn['speaker'] == 'John Smith' else 'Managers may cut spending if sales fall; this is not their main plan.' if kind == 'answer' else 'Sales rose, but costs rose faster, so profit fell.'), 'turn_ids': [turn['id']], 'quotes': [{'turn_id': turn['id'], 'quote': turn['text']}]})
        rows.append({'group_id': group['group_id'], 'bullets': bullets, 'answer_status': 'partial' if group['question_turn_ids'] else 'not_a_question'})
    return {'discussions': rows}


def test_briefs_bind_all_questions_and_responders_to_exact_words():
    groups = discussion_inputs(analyze_transcript(TEXT))
    accepted, errors = validate_discussions(response_for(groups), groups)
    assert not errors
    question = next(group for group in groups if group['question_turn_ids'])
    result = accepted[question['group_id']]
    assert result['answer_status'] == 'partial'
    assert {tid for bullet in result['bullets'] for tid in bullet['turn_ids']} == set(question['question_turn_ids'] + question['answer_turn_ids'])
    for bullet in result['bullets']:
        assert all(quote['offset'] == 0 for quote in bullet['quotes'])


@pytest.mark.parametrize('damage', ['invent_quote', 'wrong_role', 'foreign_turn', 'omit_answer', 'prepared_as_answer'])
def test_unbound_or_role_confused_summaries_are_rejected(damage):
    groups = discussion_inputs(analyze_transcript(TEXT))
    payload = response_for(groups)
    row = next(row for row in payload['discussions'] if row['answer_status'] == 'partial')
    if damage == 'invent_quote': row['bullets'][0]['quotes'][0]['quote'] = 'We will definitely cut spending.'
    if damage == 'wrong_role': row['bullets'][0]['kind'] = 'answer'
    if damage == 'foreign_turn': row['bullets'][0]['turn_ids'] = ['made-up-turn']
    if damage == 'omit_answer': row['bullets'].pop()
    if damage == 'prepared_as_answer':
        row = next(row for row in payload['discussions'] if row['answer_status'] == 'not_a_question')
        row['answer_status'] = 'answered'
    accepted, errors = validate_discussions(payload, groups)
    assert row['group_id'] not in accepted
    assert errors


def test_generation_caches_by_source_and_namespace_without_rewriting_analysis(tmp_path):
    config = Settings(data_dir=tmp_path)
    original = analyze_transcript(TEXT)
    record = AnalysisStore(config.evidence_dir).save('transcript', {'text': TEXT}, original)
    original_bytes = (config.evidence_dir / 'document-analysis' / f"{record['id']}.json").read_bytes()
    service = TranscriptBriefs(config)
    calls = []
    async def extract(prompt, schema):
        calls.append(prompt)
        return response_for(json.loads(prompt.split('\n', 1)[1]))
    async def check():
        first, concurrent = await asyncio.gather(service.generate(original, extractor=extract), service.generate(original, extractor=extract))
        assert first == concurrent
        assert first['status'] == 'completed'
        assert len(calls) == 1
        assert service.load(original)['status'] == 'completed'
        assert service.load(original, 'demo')['status'] == 'not_prepared'
        assert service.load(analyze_transcript(TEXT + '\nUpdated remarks.'))['status'] == 'not_prepared'
    asyncio.run(check())
    assert (config.evidence_dir / 'document-analysis' / f"{record['id']}.json").read_bytes() == original_bytes


def test_pause_guard_prevents_model_dispatch(tmp_path):
    service = TranscriptBriefs(Settings(data_dir=tmp_path))
    calls = []
    async def extract(*args): calls.append(args)
    def guard(): raise ValueError('paused')
    with pytest.raises(ValueError, match='paused'):
        asyncio.run(service.generate(analyze_transcript(TEXT), guard=guard, extractor=extract))
    assert not calls


def test_incomplete_original_cannot_become_a_synthetic_explanation(tmp_path):
    result = analyze_transcript(TEXT)
    result['reading_context']['full_text_available'] = False
    with pytest.raises(ValueError, match='complete saved transcript'):
        discussion_inputs(result)


def test_api_open_is_read_only_and_prepare_requires_mutation_guard(tmp_path):
    config = Settings(data_dir=tmp_path)
    record = AnalysisStore(config.evidence_dir).save('transcript', {'text': TEXT}, analyze_transcript(TEXT))
    service = TranscriptBriefs(config)
    calls = []
    async def fake_generate(result, namespace, *, guard):
        guard(); calls.append(result)
        return {'status': 'completed', 'discussions': {}, 'gaps': []}
    service.generate = fake_generate
    def guard(request: Request):
        if request.headers.get('x-road2m-client') != 'local-ui': raise HTTPException(403)
    app = FastAPI()
    app.include_router(create_document_router(config, guard, repo=SimpleNamespace(firm_paused=lambda: False), briefs=service))
    with TestClient(app) as client:
        assert client.get(f"/api/document-analysis/history/{record['id']}").json()['result']['plain_language']['status'] == 'not_prepared'
        assert not calls
        assert client.post(f"/api/document-analysis/history/{record['id']}/explain").status_code == 403
        assert client.post(f"/api/document-analysis/history/{record['id']}/explain", headers={'x-road2m-client': 'local-ui'}).status_code == 200
        assert len(calls) == 1


def test_reply_cannot_be_labelled_no_response():
    groups = discussion_inputs(analyze_transcript(TEXT))
    payload = response_for(groups)
    row = next(row for row in payload['discussions'] if row['answer_status'] == 'partial')
    row['answer_status'] = 'no_response'
    accepted, errors = validate_discussions(payload, groups)
    assert row['group_id'] not in accepted
    assert any('Management responded' in error for error in errors)


def test_ambiguous_repeated_quote_requires_more_context():
    groups = [{'group_id':'g','question_turn_ids':[],'answer_turn_ids':[], 'turns':[{'id':'t','speaker':'CFO','role':'management','text':'Sales grew. Costs grew. Sales grew.'}]}]
    payload = {'discussions':[{'group_id':'g','answer_status':'not_a_question','bullets':[{'kind':'remark','summary':'Sales grew alongside costs.','turn_ids':['t'],'quotes':[{'turn_id':'t','quote':'Sales grew.'}]}]}]}
    accepted, errors = validate_discussions(payload, groups)
    assert not accepted
    assert any('repeated quotation' in error for error in errors)


def test_brief_provider_schema_and_prompt_match_the_validator_budget(tmp_path):
    service = TranscriptBriefs(Settings(data_dir=tmp_path))
    async def extract(prompt, schema):
        sent = normalize_output_schema(schema)
        bullets = sent['properties']['discussions']['items']['properties']['bullets']
        assert bullets['minItems'] == 1 and bullets['maxItems'] == 8
        assert bullets['items']['properties']['quotes']['maxItems'] == 4
        assert bullets['items']['properties']['summary']['maxLength'] == 480
        assert 'at most 8 bullets per discussion' in prompt
        assert 'at most 4 short supporting quotes per bullet' in prompt
        assert 'Do not catalog every incidental number' in prompt
        assert 'dropping a material ask or caveat' in prompt
        assert 'Preserve all separate questions, numbers' not in prompt
        return response_for(json.loads(prompt.split('\n', 1)[1]))
    result = asyncio.run(service.generate(analyze_transcript(TEXT), extractor=extract))
    assert result['status'] == 'completed'
    assert result['attempts'][0]['prompt_version'] == PROMPT_VERSION
    assert result['attempts'][0]['validation_errors'] == []


def test_related_turns_can_share_bullets_without_losing_speaker_coverage():
    turns = [{'id': 'q', 'speaker': 'Analyst', 'role': 'analyst', 'text': 'Will costs fall, and what might prevent that?'}]
    turns += [{'id': f'a{i}', 'speaker': f'Manager {i}', 'role': 'management', 'text': f'Our area {i} expects lower costs, unless demand weakens.'} for i in range(11)]
    group = {'group_id': 'many-responders', 'question_turn_ids': ['q'], 'answer_turn_ids': [t['id'] for t in turns[1:]], 'turns': turns}
    bullets = [{'kind': 'question', 'summary': 'They ask whether costs will fall and what could stop that.', 'turn_ids': ['q'], 'quotes': [{'turn_id': 'q', 'quote': turns[0]['text']}]}]
    for start in range(1, len(turns), 4):
        selected = turns[start:start+4]
        bullets.append({'kind': 'answer', 'summary': 'These managers expect lower costs unless demand weakens.', 'turn_ids': [t['id'] for t in selected], 'quotes': [{'turn_id': t['id'], 'quote': t['text']} for t in selected]})
    payload = {'discussions': [{'group_id': group['group_id'], 'answer_status': 'answered', 'bullets': bullets}]}
    accepted, errors = validate_discussions(payload, [group])
    assert not errors and len(accepted[group['group_id']]['bullets']) == 4
    assert {tid for bullet in bullets for tid in bullet['turn_ids']} == {t['id'] for t in turns}


def test_explicit_retry_only_generates_rejected_groups_and_retains_history(tmp_path):
    service = TranscriptBriefs(Settings(data_dir=tmp_path))
    original = analyze_transcript(TEXT)
    groups = discussion_inputs(original)
    rejected = next(g['group_id'] for g in groups if g['question_turn_ids'])
    calls = []
    async def extract(prompt, schema):
        requested = json.loads(prompt.split('\n', 1)[1])
        calls.append([g['group_id'] for g in requested])
        payload = response_for(requested)
        if len(calls) == 1:
            row = next(row for row in payload['discussions'] if row['group_id'] == rejected)
            row['bullets'] = [row['bullets'][0]] * 9
        return payload
    first = asyncio.run(service.generate(original, extractor=extract))
    assert first['status'] == 'partial' and rejected not in first['discussions']
    first_attempt = dict(first['attempts'][0])
    assert first_attempt['validation_errors']
    second = asyncio.run(service.generate(original, extractor=extract))
    assert calls[1] == [rejected]
    assert second['status'] == 'completed' and len(second['attempts']) == 2
    assert second['attempts'][0] == first_attempt
    assert second['retry_history'][0]['gaps'] == first['gaps']
    assert second['cache_key'] == first['cache_key']
    assert all(second['discussions'][gid] == discussion for gid, discussion in first['discussions'].items())


def test_impossible_quotation_coverage_is_a_gap_without_a_model_call(tmp_path, monkeypatch):
    from backend.app.research import transcript_briefs as module
    turns = [{'id': f't{i}', 'speaker': 'Manager', 'role': 'management', 'text': f'Costs may change in region {i}.'} for i in range(33)]
    groups = [{'group_id': 'too-many-turns', 'question_turn_ids': [], 'answer_turn_ids': [], 'turns': turns}]
    monkeypatch.setattr(module, 'discussion_inputs', lambda result: groups)
    calls = []
    async def extract(*args):
        calls.append(args)
        return {'discussions': []}
    result = asyncio.run(TranscriptBriefs(Settings(data_dir=tmp_path)).generate(analyze_transcript(TEXT), extractor=extract))
    assert result['status'] == 'partial' and not calls
    assert any('eight-bullet quotation limit' in gap for gap in result['gaps'])
