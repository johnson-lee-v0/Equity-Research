import base64
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api.document_intelligence import create_document_router
from backend.app.config import Settings
from backend.app.research.document_intelligence import (
    AnalysisStore, analyze_transcript, compare_filings, import_document,
    plain_text, sentiment,
)


TRANSCRIPT = """Jane Doe — Chief Executive Officer
We saw strong demand for Microsoft Azure in Europe. We are not confident about near-term growth.
John Roe — Chief Financial Officer
Our liquidity remains strong and our margins improved.
Operator: Thank you for the strong and successful quarter.
Questions and Answers
Alice Smith — Analyst: Are you seeing weakness in China?
Jane Doe: We see no weakness in China, but costs remain under pressure.
"""


def test_transcript_sentiment_has_negation_speaker_roles_and_exact_evidence():
    result = analyze_transcript(TRANSCRIPT, "MSFT")
    assert len(result['sentences']) == 6
    assert result['sentiment']['sentences'] == 5  # operator excluded
    speakers = {item['name']: item for item in result['speakers']}
    assert speakers['Jane Doe']['role'] == 'management'
    assert speakers['Alice Smith']['role'] == 'analyst'
    assert speakers['Operator']['role'] == 'operator'
    negated = next(item for item in result['sentences'] if 'not confident' in item['text'])
    assert negated['sentiment']['label'] == 'negative'
    assert any(cue['term'] == 'confident' and cue['negated'] for cue in negated['sentiment']['cues'])
    assert next(item for item in result['sentences'] if 'no weakness' in item['text'])['section'] == 'Q&A'
    azure = next(item for item in result['entities'] if item['name'] == 'Microsoft Azure')
    evidence = azure['evidence'][0]
    assert evidence['text'] == result['sentences'][evidence['sentence_id'] - 1]['text']
    assert evidence['speaker'] == 'Jane Doe'
    assert any(item['name'] == 'Europe' and item['type'] == 'location' for item in result['entities'])


@pytest.mark.parametrize(('text', 'label'), [
    ('Demand is not strong.', 'negative'),
    ('We do not expect a decline.', 'positive'),
    ('Demand is not only strong but improving.', 'positive'),
    ('There is no weakness, but demand is strong.', 'positive'),
    ("We aren't confident about demand.", 'negative'),
    ('Stronghold reported its quarterly results.', 'neutral'),
])
def test_negation_scopes_and_word_boundaries(text, label):
    assert sentiment(text)['label'] == label


def test_html_reads_visible_prose_and_keeps_narrative_in_layout_tables():
    text = '<html><head><title>Ignore me</title><script>x()</script></head><body><div hidden>secret</div><ix:hidden><xbrli:context>invisible</xbrli:context></ix:hidden><table><tr><td><p>Demand remained strong in our core business.</p></td></tr></table><script>alert(1)</script></body></html>'
    assert plain_text(text) == 'Demand remained strong in our core business.'


PREVIOUS = """Item 1. Financial Statements
Consolidated balance sheets
Cash 100 200
Notes to financial statements
The tax provision was subject to various adjustments.
Item 2. Management's Discussion and Analysis
Revenue increased to $100 million for the quarter ended June 30, 2026.
We expect customer demand to remain strong.
Our liquidity remains adequate for our needs.
Part II. Other Information
Item 1A. Risk Factors
We may experience supply disruptions in Europe.
"""
CURRENT = """Item 1. Financial Statements
Consolidated balance sheets
Cash 300 400
Notes to financial statements
Our tax provision changed after the transaction.
Item 2. Management's Discussion and Analysis
Revenue increased to $200 million for the quarter ended September 30, 2026.
We no longer expect customer demand to remain strong.
Our liquidity remains adequate for our needs.
Part II. Other Information
Item 1A. Risk Factors
We are experiencing material supply disruptions in Europe.
Regulatory scrutiny may adversely affect our international business.
"""


def test_comparison_excludes_statements_and_numeric_changes_retains_qualitative_risk():
    result = compare_filings(PREVIOUS, CURRENT)
    assert result['counts']['numeric_only'] == 1
    assert result['counts']['changed'] == 2
    assert result['counts']['added'] == 1
    assert result['counts']['removed'] == 0
    assert result['counts']['unchanged'] == 1
    assert result['exclusions']['previous']['financial_statement_lines'] > 0
    output = str(result['changes'])
    assert 'tax provision' not in output and '$200' not in output
    assert 'no longer expect' in output and 'material supply disruptions' in output
    risk = next(item for item in result['changes'] if 'supply disruptions' in item['after'])
    assert risk['type'] == 'changed' and risk['significance'] == 'Risk language — review'
    assert 'Item 1A' in risk['section']


def test_moved_paragraphs_and_duplicate_paragraphs_do_not_generate_spurious_changes():
    a = 'We expect demand to remain strong.\nLiquidity remains sufficient for our needs.\nWe expect demand to remain strong.'
    b = 'Liquidity remains sufficient for our needs.\nWe expect demand to remain strong.\nWe expect demand to remain strong.'
    result = compare_filings(a, b)
    assert result['changes'] == []
    assert result['counts']['unchanged'] == 3


def test_qualitative_financial_narrative_with_numbers_is_retained():
    result = compare_filings('Revenue increased 10% and margins were strong at 20%.', 'Revenue decreased 15% and margins were weak at 12%.')
    assert result['counts']['changed'] == 1
    assert 'margins were weak' in result['changes'][0]['after']


def test_standalone_item_headings_and_toc_do_not_hide_mda():
    prior = 'Item 1. Financial Statements 3\nItem 2. Management Discussion 25\nPart I\nItem 1\nFinancial Statements\nCash 1 2\nItem 2\nManagement Discussion\nCustomer demand remains strong across our core markets.'
    current = prior.replace('demand remains strong', 'demand has weakened')
    result = compare_filings(prior, current)
    assert len(result['changes']) == 1
    assert result['counts']['changed'] == 1
    assert result['exclusions']['previous']['table_of_contents'] == 2


def test_same_sentence_in_different_sec_items_is_not_cross_matched():
    previous = 'Part II\nItem 1. Legal Proceedings\nWe are subject to ongoing regulatory scrutiny.\nItem 1A. Risk Factors\nDemand remains strong in core markets.'
    current = 'Part II\nItem 1. Legal Proceedings\nDemand remains strong in core markets.\nItem 1A. Risk Factors\nWe are subject to ongoing regulatory scrutiny.'
    result = compare_filings(previous, current)
    assert result['counts']['unchanged'] == 0
    assert result['counts']['added'] == result['counts']['removed'] == 2


def test_import_has_limits_and_no_html_execution():
    encoded = base64.b64encode(b'<p>We remain confident about customer demand.</p><script>alert(1)</script>').decode()
    result = import_document('call.html', encoded)
    assert result['format'] == 'html'
    assert result['text'] == 'We remain confident about customer demand.'
    with pytest.raises(ValueError, match='valid base64'):
        import_document('call.txt', '!!!')
    with pytest.raises(ValueError, match='Supported imports'):
        import_document('script.exe', base64.b64encode(b'anything').decode())


def test_private_persistence_roundtrips_originals_and_limits_history(tmp_path):
    store = AnalysisStore(tmp_path / 'evidence')
    record = store.save('transcript', {'text': TRANSCRIPT, 'title': 'Quarter call', 'ticker': 'MSFT'}, analyze_transcript(TRANSCRIPT))
    assert store.get(record['id']) == record
    assert store.directory == tmp_path / 'evidence' / 'document-analysis'
    assert record['inputs']['text'] == TRANSCRIPT
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert (store.directory / (record['id'] + '.json')).stat().st_mode & 0o777 == 0o600
    assert 'inputs' not in store.history()[0]
    assert store.get('../../other') is None
    # Corrupt file must not break otherwise valid history.
    (store.directory / 'invalid.json').write_text('{broken')
    assert len(store.history()) == 1


def test_api_contract_history_detail_and_mutation_guard(tmp_path):
    config = Settings(data_dir=tmp_path)
    def guard(request: Request):
        if request.headers.get('x-road2m-client') != 'local-ui':
            raise HTTPException(status_code=403, detail='Required client header missing')
    app = FastAPI()
    app.include_router(create_document_router(config, guard))
    with TestClient(app) as client:
        assert client.post('/api/document-analysis/transcript', json={'text': TRANSCRIPT}).status_code == 403
        response = client.post('/api/document-analysis/transcript', json={'text': TRANSCRIPT, 'title': 'Example'}, headers={'x-road2m-client': 'local-ui'})
        assert response.status_code == 200
        record = response.json()
        assert client.get('/api/document-analysis/history').json()['items'][0]['id'] == record['id']
        assert client.get('/api/document-analysis/history/' + record['id']).json() == record
        assert client.get('/api/document-analysis/history/missing').status_code == 404
        comparison = client.post('/api/document-analysis/compare', json={'previous_text': PREVIOUS, 'current_text': CURRENT}, headers={'x-road2m-client': 'local-ui'})
        assert comparison.status_code == 200
        assert comparison.json()['kind'] == 'filing_comparison'
        invalid = client.post('/api/document-analysis/transcript', json={'text': 'too short'}, headers={'x-road2m-client': 'local-ui'})
        assert invalid.status_code == 422


def test_empty_or_only_financial_filings_rejected():
    with pytest.raises(ValueError, match='No comparable narrative'):
        compare_filings('Item 1. Financial Statements\nCash 100 200', 'Item 1. Financial Statements\nCash 200 300')


def test_financial_shorthand_changes_ignored_but_references_and_products_retained():
    before = 'We generated $5m of revenue in the quarter.\nWe continue to face tariffs under Section 301.\nOur current product integrates GPT-4 for customer support.'
    after = 'We generated $6m of revenue in the quarter.\nWe continue to face tariffs under Section 232.\nOur current product integrates GPT-5 for customer support.'
    result = compare_filings(before, after)
    assert result['counts']['numeric_only'] == 1
    assert result['counts']['changed'] == 2
    assert any('Section 232' in item['after'] for item in result['changes'])
    assert any('GPT-5' in item['after'] for item in result['changes'])


def test_material_weakness_negation_change_survives_normalization():
    result = compare_filings('We identified no material weakness in our internal controls.', 'We identified a material weakness in our internal controls.')
    assert result['counts']['changed'] == 1
    assert result['changes'][0]['significance'] == 'Risk language — review'


def test_iso_and_us_date_only_changes_are_excluded():
    result = compare_filings('As of 2026-06-30, we remain confident about demand.\nOur review dated 06/30/2026 found no material weakness.', 'As of 2026-09-30, we remain confident about demand.\nOur review dated 09/30/2026 found no material weakness.')
    assert result['changes'] == []
    assert result['counts']['numeric_only'] == 2


def test_pdf_import_reuses_bounded_extractor_and_preserves_warnings():
    from backend.tests.test_pdf_evidence import pdf
    result = import_document('filing.pdf', base64.b64encode(pdf(blank_second=True)).decode())
    assert result['format'] == 'pdf'
    assert 'EXMP FY2025 revenue USD 42 million.' in result['text']
    assert any('OCR was not performed' in warning for warning in result['warnings'])
    with pytest.raises(ValueError, match='no extractable text'):
        import_document('scan.pdf', base64.b64encode(pdf(text=False)).decode())


def test_financial_subheadings_inside_mda_keep_liquidity_commentary():
    before = 'Item 1. Financial Statements\nCash 100 200\nItem 2. Management Discussion\nConsolidated Statements of Cash Flows\nOur cash generation remains strong across the business.\nLiquidity and Capital Resources\nOur available liquidity is adequate for near-term needs.\nPart II\nItem 1A. Risk Factors\nRisk Factors Summary\nOur operating environment remains uncertain.\nItem 6. Exhibits\nThis agreement is incorporated herein by reference.'
    after = before.replace('liquidity is adequate', 'liquidity is not adequate').replace('incorporated herein by reference', 'newly filed with the Commission')
    result = compare_filings(before, after)
    assert result['counts']['changed'] == 1
    assert 'Part I · Item 2' in result['changes'][0]['section']
    assert result['exclusions']['current']['exhibits_and_signatures'] > 0


def test_html_size_limit_applies_to_readable_text_after_markup():
    # SEC filings contain large styling/inline-XBRL overhead; readable text
    # below 1m must still import when HTML itself exceeds 1m characters.
    raw = '<html>' + '<span style="' + 'padding:0;' * 110_000 + '">We remain confident about demand in our core markets.</span></html>'
    assert len(raw) > 1_000_000
    assert plain_text(raw) == 'We remain confident about demand in our core markets.'


def test_sentence_initial_business_words_and_questions_are_not_entities():
    text = 'Jane Doe — CEO\nCustomer demand remained strong and growth improved. Microsoft expanded its partnership with Acme Corporation in Europe. Costs increased and margins declined.\nQuestions and Answers\nAlice Smith — Analyst: Do you expect stronger demand in Europe?'
    result = analyze_transcript(text)
    names = {item['name'] for item in result['entities']}
    assert not names & {'Do', 'Customer', 'Costs'}
    assert {'Microsoft', 'Acme Corporation', 'Europe'} <= names
