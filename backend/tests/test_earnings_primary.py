"""Primary recovery proves periods/units and keeps historical gaps distinct."""
import asyncio
import hashlib
import copy
import json
from types import SimpleNamespace

import pytest

from backend.app.research.earnings_primary import primary_release_points, recover_primary_trends
from backend.app.research.earnings_trends import METRICS
from backend.tests.test_assessment_evidence import RELEASE

COMPANY = {'ticker': 'ABC', 'name': 'ABC Corporation', 'cik': '0000001234', 'website': 'https://abc.com'}
DOC = {'source_id': 'ir', 'url': 'https://investor.abc.com/results', 'fiscal_period': 'Q4 FY2025', 'period_end': '2025-12-28', 'published_at': '2026-01-15'}
TEXT = 'ABC fourth quarter fiscal 2025 operating results January 15, 2026\nNet sales for the quarter increased 8.0 percent, to $93.9 billion, from $84.4 billion last year.\n' + RELEASE.replace('Net income 100 90 390 350', 'Net sales 100 90 390 350\nMerchandise costs 80 73 310 283\nNet income 100 90 390 350') + '''\nABC CORPORATION
CONSOLIDATED STATEMENTS OF CASH FLOWS
(amounts in millions) (unaudited)
52 Weeks Ended
December 28,
2025  December 29,
2024
CASH FLOWS FROM OPERATING ACTIVITIES
Net income 390 350
CASH FLOWS FROM INVESTING ACTIVITIES
Additions to property and equipment (6,435 ) (5,498 )
'''


def test_income_table_ratio_and_cash_flow_retain_actual_operands_and_currency_proof():
    row = {'start': '2024-01-01', 'end': '2024-12-29', 'val': 5498000000, 'form': '10-K', 'filed': '2025-02-01'}
    anchor = {'period_end': '2024-12-29', 'value': 5.498, 'source_id': 'sec', 'quote': json.dumps(row)}
    cash_sources = {'sec': {'url': 'https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json', 'content': json.dumps({'cik': 1234, 'taxonomy': 'us-gaap', 'tag': 'PaymentsToAcquirePropertyPlantAndEquipment', 'units': {'USD': [row]}})}}
    points = primary_release_points(DOC | {'content': TEXT}, COMPANY, [anchor], cash_sources=cash_sources)
    assert [(point['metric'], point['value']) for point in points] == [('net_sales_growth', 8), ('gross_margin', 20), ('capex_cash_ppe', 6.435)]
    assert points[0]['quote'].endswith('$84.4 billion last year.')
    assert points[1]['calculation']['inputs'] == {'net_sales': '100', 'merchandise_costs': '80'}
    assert points[2]['currency_binding']['source_id'] == 'sec'
    wrong_currency = copy.deepcopy(cash_sources)
    wrong_currency['sec']['content'] = wrong_currency['sec']['content'].replace('"USD"', '"CAD"')
    assert not any(point['metric'] == 'capex_cash_ppe' for point in primary_release_points(DOC | {'content': TEXT}, COMPANY, [anchor], cash_sources=wrong_currency))
    assert all(point['kind'] == 'actual' and point['source_id'] == 'ir' for point in points)
    assert 'capex_cash_ppe' not in {point['metric'] for point in primary_release_points(DOC | {'content': TEXT}, COMPANY)}
    for changed in [anchor | {'value': 5.5}, anchor | {'period_end': '2024-12-30'}]:
        assert 'capex_cash_ppe' not in {point['metric'] for point in primary_release_points(DOC | {'content': TEXT}, COMPANY, [changed])}
    assert any(point['metric'] == 'capex_cash_ppe' for point in primary_release_points(DOC | {'content': TEXT}, COMPANY, [], cash_sources=cash_sources))


def test_primary_parser_rejects_wrong_issuer_source_and_year_to_date_cash_flow():
    assert not primary_release_points(DOC | {'content': TEXT, 'url': 'https://abc.example.com/results'}, COMPANY)
    assert not primary_release_points(DOC | {'content': TEXT.replace('ABC', 'XYZ')}, COMPANY)
    points = primary_release_points(DOC | {'content': TEXT.replace('52 Weeks Ended\nDecember', '39 Weeks Ended\nDecember')}, COMPANY, [{'period_end': '2024-12-29', 'value': 5.498, 'source_id': 'sec'}])
    assert not any(point['metric'] == 'capex_cash_ppe' for point in points)
    points = primary_release_points(DOC | {'content': TEXT, 'period_end': '2025-12-29'}, COMPANY)
    assert not any(point['metric'] == 'gross_margin' for point in points)


def test_reported_flat_revenue_is_zero_with_exact_primary_quote_and_quarter():
    quote = 'Third quarter revenues were $11.3 billion, flat on a reported basis and down 3 percent on a currency-neutral basis.'
    document = DOC | {'fiscal_period': 'Q3 FY2026', 'period_end': '2026-02-28', 'published_at': '2026-03-31',
        'content': 'ABC Corporation reported fiscal 2026 third quarter results. March 31, 2026\n' + quote}
    points = primary_release_points(document, COMPANY)
    assert len(points) == 1 and points[0]['metric'] == 'net_sales_growth' and points[0]['value'] == 0
    assert points[0]['quote'] == quote and points[0]['period'] == 'Q3 FY2026'
    assert points[0]['source_id'] == document['source_id'] and points[0]['url'] == document['url']
    assert 'reported precision' in points[0]['calculation']['formula']
    for changed in (quote.replace('Third quarter', 'Second quarter'),
                    quote.replace('revenues were', 'wholesale revenues were'),
                    quote.replace('flat on a reported basis', 'flat on a currency-neutral basis'),
                    quote.replace('flat on a reported basis', 'approximately flat on a reported basis'),
                    quote.replace('were', 'are expected to be'),
                    quote.replace('Third quarter', 'Full year')):
        assert not primary_release_points(document | {'content': document['content'].replace(quote, changed)}, COMPANY)
    assert not primary_release_points(document | {'fiscal_period': 'Q3 FY2025'}, COMPANY)


def test_explicit_annual_management_capex_requires_annual_period_usd_and_actual_wording():
    quote = 'Capital expenditures for the full year were USD 6.435 billion.'
    document = DOC | {'content': 'ABC Corporation fourth quarter fiscal 2025 results.\n' + quote}
    points = primary_release_points(document, COMPANY)
    assert [(p['metric'], p['value'], p['period']) for p in points] == [('capex', 6.435, 'FY2025')]
    assert points[0]['quote'] == quote
    for changed in [quote.replace('full year', 'fourth quarter'), quote.replace('USD', 'CAD'),
                    quote.replace('USD', '$'), quote.replace('were', 'are expected to be')]:
        assert not primary_release_points(document | {'content': document['content'].replace(quote, changed)}, COMPANY)


def test_missing_actual_cash_spending_triggers_bounded_primary_recovery_even_without_sales_gaps():
    calls = []
    class Acquisition:
        async def _discover(self, stage, prompt, schema):
            calls.append((stage, prompt))
            return {'sources': []}
    points = {metric: [] for metric in METRICS}
    result = asyncio.run(recover_primary_trends(Acquisition(), COMPANY, {'fiscal_period': 'Q4 FY2025', 'earnings_date': '2026-01-15'}, [], points))
    assert len(calls) == 1 and calls[0][0] == 'primary_trend_recovery'
    assert 'capex_cash_ppe' in calls[0][1] and 'annual report/10-K' in calls[0][1]
    assert len(result['gaps']) == 5 and all('capex_cash_ppe' in gap for gap in result['gaps'])


def test_extraction_failure_is_reported_as_evidence_gap_never_a_projection():
    class Acquisition:
        async def _discover(self, *args):
            raise RuntimeError('No candidates available')
    points = {metric: [] for metric in METRICS}
    points['net_sales_growth'] = [{'period': 'Q4 FY2025', 'value': 8, 'kind': 'actual'}]
    result = asyncio.run(recover_primary_trends(Acquisition(), COMPANY, {'fiscal_period': 'Q4 FY2025', 'earnings_date': '2026-01-15'}, [], points))
    assert result['gaps'] and not result['points']
    assert 'Primary-source recovery' in result['gaps'][0]


# Use the durable workflow fixture to verify the refresh lifecycle rather than
# mocking the store. The original package has a linked research handoff.
from backend.tests.test_research_workflows import setup, complete


def test_source_refresh_forks_linked_package_without_replaying_analysis(setup):
    service, acquisition, repo = setup
    original = acquisition.acquire
    async def hashed(company, event):
        output = await original(company, event)
        for document in output['documents'].values():
            document['content_hash'] = hashlib.sha256(document['content'].encode()).hexdigest()
        return output
    acquisition.acquire = hashed
    parent = complete(service)
    service.handoff(parent['id'])
    before = service.store.get(parent['id'])
    refreshed = service.store.refresh_sources(parent['id'], candidates=[])
    assert refreshed != parent['id']
    assert service.store.refresh_sources(parent['id']) == refreshed
    child = service.store.get(refreshed)
    assert child['source_refresh_of'] == parent['id']
    assert next(row for row in service.store.history() if row['id'] == refreshed)['source_refresh_of'] == parent['id']
    steps = {step['id']: step for step in child['steps']}
    assert steps['locate']['status'] == steps['transcript']['status'] == 'completed'
    assert steps['acquire']['status'] == steps['filings']['status'] == 'pending'
    assert steps['trends']['status'] == 'pending' and steps['trends']['output']['_refresh_from'] == parent['id']
    assert steps['context']['status'] == 'pending' and steps['publish']['status'] == 'pending'
    assert service.store.get(parent['id']) == before
    assert acquisition.calls == ['resolve', 'locate', 'acquire']
    assert child['research_run_id'] is None
    # Executing the revision retries only the missing-document boundary and
    # deterministic figures. The call and frozen investment handoff stay put.
    async def refresh_filings(company, event, prior, *, research_as_of=None):
        assert research_as_of == child['created_at']
        acquisition.calls.append('refresh_missing_filings')
        return copy.deepcopy(prior)
    async def refresh_figures(company, event, prior, documents, *, candidates=None, research_as_of=None):
        assert candidates == []
        assert research_as_of == child['created_at']
        return copy.deepcopy(parent['result']['trends']) | {'source_refresh': {'parent_workflow_id': parent['id']}}
    acquisition.refresh_missing_filings = refresh_filings
    service.trends_factory = lambda _: SimpleNamespace(refresh_primary=refresh_figures)
    asyncio.run(service.execute(refreshed))
    updated = service.store.get(refreshed)
    assert updated['status'] == 'completed'
    assert updated['source_refresh_of'] == parent['id']
    assert next(row for row in service.store.history() if row['id'] == refreshed)['source_refresh_of'] == parent['id']
    assert acquisition.calls == ['resolve', 'locate', 'acquire', 'refresh_missing_filings']
    assert service.store.get(parent['id']) == before
    assert next(step for step in updated['steps'] if step['id'] == 'transcript') == steps['transcript']
    # A changed retained source cannot be reused against the old frozen report.
    sid = parent['result']['source_ids'][0]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content||'changed',content_hash='changed' WHERE id=?", (sid,))
    with pytest.raises(ValueError, match='snapshot|changed'):
        service.store.refresh_sources(parent['id'])


def test_repeated_primary_refresh_repairs_quote_without_changing_actual_value():
    from backend.app.research.earnings_trends import EarningsTrends
    row = {'start': '2024-01-01', 'end': '2024-12-29', 'val': 5498000000, 'form': '10-K', 'filed': '2025-02-01'}
    sec = {'id': 'sec', 'url': 'https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json', 'content': json.dumps({'cik': 1234, 'taxonomy': 'us-gaap', 'tag': 'PaymentsToAcquirePropertyPlantAndEquipment', 'units': {'USD': [row]}})}
    source = {'id': 'ir', 'content': TEXT, 'url': DOC['url'], 'content_hash': hashlib.sha256(TEXT.encode()).hexdigest()}
    sec['content_hash'] = hashlib.sha256(sec['content'].encode()).hexdigest()
    sources = {'ir': source, 'sec': sec}
    class Repo:
        def source_packet(self, namespace, ids):
            return [sources[sid] for sid in ids]
    acquisition = SimpleNamespace(repo=Repo(), namespace='real')
    document = DOC | {'status': 'available', 'content_hash': source['content_hash']}
    old_point = {'source_id': 'ir', 'url': DOC['url'], 'period': 'FY2025', 'period_end': DOC['period_end'], 'value': 6.435, 'kind': 'actual', 'quote': 'Noncontiguous header plus row', 'published_at': DOC['published_at'], 'recovery': 'earnings-primary-backfill.v1', 'currency_binding': {'source_id': 'sec'}, 'low': None, 'high': None}
    resolved_gap = 'Q4 FY2025 capex_cash_ppe: primary-source recovery did not verify the reported value; this is an evidence gap, not a projection or zero.'
    prior = {'series': [{'id': 'capex_cash_ppe', 'points': [old_point]}], 'sources': [document, {'source_id': 'sec', 'url': sec['url']}], 'gaps': [resolved_gap]}
    event = {'fiscal_period': DOC['fiscal_period'], 'period_end': DOC['period_end'], 'earnings_date': DOC['published_at']}
    result = asyncio.run(EarningsTrends(acquisition).refresh_primary(COMPANY, event, prior, {'release': document}, candidates=[]))
    updated = next(point for series in result['series'] if series['id'] == 'capex_cash_ppe' for point in series['points'] if point['period'] == 'FY2025')
    assert updated['value'] == old_point['value']
    assert updated['quote'] in TEXT and updated['quote'] != old_point['quote']
    assert updated['recovery'] == 'earnings-primary-backfill.v3'
    assert resolved_gap not in result['gaps']
    assert prior['series'][0]['points'][0]['quote'] == 'Noncontiguous header plus row'
