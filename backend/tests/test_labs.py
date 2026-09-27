"""Behavioral coverage for the retained disclosure/replay core and its new API."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.labs import create_labs_router
from backend.app.config import Settings
from backend.app.research.lab_data import LabData, import_archive
from backend.app.research.lab_engine.backtest import Engine, kelly
from backend.app.research.lab_engine.rotation_backtest import RotationEngine
from backend.app.research.lab_engine.senate_backtest import SenateEngine
from backend.app.research.lab_engine.trade_entries import entry_ledger
from backend.app.research.labs import LabsService

DAYS = ['2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08','2025-01-09','2025-01-10','2025-01-13']


def row(identifier, **changes):
    return {'id':identifier,'politicianId':'senate_person','politician':'Member','chamber':'senate','owner':'Self',
            'dependentChildId':None,'account':None,'priceSymbol':'AAA','ticker':'AAA','tickerReported':'AAA','asset':'AAA stock',
            'assetClass':'equity','eligible':True,'exclusion':'','action':'buy','transactionType':'Purchase','amountRange':'$1,001 - $15,000',
            'tradeDate':'2025-01-02','filedDate':'2025-01-03','sourceVerified':True,'source':'https://efdsearch.senate.gov/', 'signalStatus':'new', **changes}


def bars(high=102., low=98., close=100.):
    return {d:{'date':d,'open':100.,'high':high,'low':low,'close':close,'quote':{'open':100.,'high':high,'low':low,'close':close}} for d in DAYS}


def histories(rows):
    return {symbol:{'bars':bars()} for symbol in {r['priceSymbol'] for r in rows}|{'SPY'}}


@pytest.fixture
def imported(tmp_path):
    source = tmp_path/'old'; folder=source/'public/data'; folder.mkdir(parents=True)
    rows=[row('buy'),row('sale',action='sell',transactionType='Sale (Full)',tradeDate='2025-01-06',filedDate='2025-01-07'),row('unknown',owner=None,eligible=False,exclusion='Unknown instrument',politician='=IMPORT("test")')]
    (folder/'records.json').write_text(json.dumps(rows))
    config=Settings(data_dir=tmp_path/'app')
    target=config.evidence_dir/'labs/congress'
    import_archive(source,target,include_prices=False)
    return config,rows


def test_import_preserves_lossless_evidence_and_indexed_filters(imported):
    config,rows=imported; data=LabData(config.evidence_dir/'labs/congress')
    assert data.manifest()['records']==3
    assert data.record('buy')==rows[0]
    assert data.rows(action='sell',date='2025-01-06')['total']==0
    assert data.rows(owner='Self',search='AAA')['total']==2
    assert data.rows(limit=1,offset=0)['items'][0]['id']=='sale'
    assert data.rows(eligible=False)['items'][0]['exclusion']=='Unknown instrument'


def test_api_pagination_evidence_csv_guards_and_empty_history(imported):
    config,_=imported
    def guard(): raise HTTPException(403,'write blocked')
    app=FastAPI();app.include_router(create_labs_router(config,guard))
    with TestClient(app) as client:
        assert client.get('/api/labs/catalog').json()['available'] is True
        page=client.get('/api/labs/congress/records?limit=1&date=2025-01-07').json()
        assert page['total']==3 and len(page['items'])==1
        assert client.get('/api/labs/congress/records/buy').json()['id']=='buy'
        assert client.get('/api/labs/congress/records/absent').status_code==404
        assert client.get('/api/labs/congress/records?limit=1000').status_code==422
        assert client.get('/api/labs/backtests').json()=={'items':[]}
        assert client.post('/api/labs/backtests',json={}).status_code==403
        assert "'=IMPORT" in client.get('/api/labs/congress/export.csv').text
        assert client.get('/api/labs/congress/positions?date=2025-01-07').status_code==200


def test_rotation_caps_positions_cash_and_reconciles_every_purchase():
    rows=[row(f'b{i:02}',priceSymbol=f'S{i:02}',ticker=f'S{i:02}') for i in range(24)]
    engine=RotationEngine(rows,histories(rows))
    result=engine.simulate_rotation(rows,start=DAYS[0],end=DAYS[-1],fee_bps=17)
    entries=entry_ledger(result,{r['id']:r for r in rows},engine.prices)
    assert result['rotation']['peakPositions']==20
    assert result['rotation']['pendingCount']==0
    assert result['executedBuys']==24
    assert min(p['cash'] for p in result['curve'])>=-1e-7
    assert sum(p['pnl'] for p in entries)+100000==pytest.approx(result['metrics']['endingValue'])
    assert sum(p['entryFee']+p['exitFee'] for p in entries)==pytest.approx(result['fees'])
    assert result['cashDiagnostics']['cashReconciliationError']==pytest.approx(0,abs=1e-4)


def test_future_disclosure_and_same_day_date_only_sale_do_not_veto_entry():
    for filed in ('2025-01-06','2025-01-09'):
        rows=[row('buy'),row('sale',action='sell',tradeDate='2025-01-03',filedDate=filed)]
        result=SenateEngine(rows,histories(rows)).simulate_senate(rows,start=DAYS[0],end=DAYS[-1],short_holding=False)
        assert result['executedBuys']==1
        assert next(o for o in result['orders'] if o['action']=='buy')['date']=='2025-01-06'


def test_fee_slider_changes_portfolio_economics_in_all_engines():
    rows=[row('buy'),row('sale',action='sell',tradeDate='2025-01-07',filedDate='2025-01-08')]
    history=histories(rows)
    engines=[(Engine(rows,{s:h['bars'] for s,h in history.items()}),'simulate',{}),
             (SenateEngine(rows,history),'simulate_senate',{'short_holding':False}),
             (RotationEngine(rows,history),'simulate_rotation',{})]
    for engine,method,options in engines:
        free=getattr(engine,method)(rows,start=DAYS[0],end=DAYS[-1],fee_bps=0,**options)
        costly=getattr(engine,method)(rows,start=DAYS[0],end=DAYS[-1],fee_bps=25,**options)
        assert free['fees']==0
        assert costly['fees']>0
        assert costly['metrics']['endingValue']<free['metrics']['endingValue']


def test_missing_exact_prices_and_invalid_sources_never_get_substitutes():
    rows=[row('missing',priceSymbol='MISS'),row('bad',eligible=False,exclusion='Unresolved identity')]
    history=histories(rows); history['MISS']['bars']={}
    result=RotationEngine(rows,history).simulate_rotation(rows,start=DAYS[0],end=DAYS[-1])
    assert result['executedBuys']==0
    assert {r['id'] for r in result['exclusions']}=={'missing','bad'}
    assert result['metrics']['endingValue']==100000


def test_kelly_only_sees_prior_closed_outcomes():
    future=[{'date':'2025-01-10','return':1} for _ in range(50)]
    assert kelly(future,'2025-01-10')['observations']==0


def test_service_honors_publication_timestamp_and_persists_result(tmp_path, monkeypatch):
    source=tmp_path/'old'; (source/'public/data').mkdir(parents=True)
    rows=[row('late-publication',firstPublicAt='2025-01-07T22:00:00Z')]
    (source/'public/data/records.json').write_text(json.dumps(rows))
    config=Settings(data_dir=tmp_path/'app')
    import_archive(source,config.evidence_dir/'labs/congress',include_prices=False)
    monkeypatch.setattr('backend.app.research.labs.load_ohlc',lambda symbol:{'bars':bars()})
    service=LabsService(config)
    assert service.positions(date='2025-01-06')['total']==0
    request=dict(strategy='rotation_20',politician='all',chamber='all',start=DAYS[0],end=DAYS[-1],sizing='fixed_2',delay=1,add_purchases=True,short_holding=False,exit_price='next_low',fee_bps=10)
    result=service.backtest(request)
    assert result['executedBuys']==1
    assert result['entries'][0]['entryDate']=='2025-01-08'
    assert service.result(result['id'])==result
    assert service.history()['items'][0]['id']==result['id']
    assert service.data.record('late-publication')['filedDate']=='2025-01-03'


def test_refresh_preserves_prior_generation_and_failed_import_leaves_current(imported, tmp_path):
    config, old_rows = imported
    target=config.evidence_dir/'labs/congress'
    source=tmp_path/'old'
    updated=[*old_rows,row('new',filedDate='2025-01-09')]
    (source/'public/data/records.json').write_text(json.dumps(updated))
    # Ordinary startup/import stays idempotent; refresh is explicit.
    assert import_archive(source,target,include_prices=False)['records']==3
    assert import_archive(source,target,include_prices=False,refresh=True)['records']==4
    prior=next((target.parent/'congress-revisions').iterdir())
    assert LabData(prior).manifest()['records']==3
    assert LabData(prior).record('buy')==old_rows[0]
    (source/'public/data/records.json').write_text('[{"id":"broken"}')
    with pytest.raises((ValueError,KeyError)):
        import_archive(source,target,include_prices=False,refresh=True)
    assert LabData(target).manifest()['records']==4
    assert LabData(target).record('new')['id']=='new'


def test_rotation_date_only_sale_on_deferred_entry_day_is_not_known_at_open():
    rows=[row(f'b{i:02}',priceSymbol=f'S{i:02}',ticker=f'S{i:02}') for i in range(21)]
    rows.append(row('same-day-sale',priceSymbol='S20',action='sell',tradeDate='2025-01-07',filedDate='2025-01-07'))
    result=RotationEngine(rows,histories(rows)).simulate_rotation(rows,start=DAYS[0],end=DAYS[-1],exit_price='hold')
    assert result['executedBuys']==21


def test_recorded_filing_timezone_survives_projection_and_is_honored(tmp_path, monkeypatch):
    import sqlite3
    import zlib
    from backend.app.research.lab_data import atomic_json, load_json
    source=tmp_path/'old'; (source/'public/data').mkdir(parents=True)
    rows=[row('local-filing',filedAt='2025-01-03T13:11:00',filedTimeZoneAssumption='America/New_York')]
    (source/'public/data/records.json').write_text(json.dumps(rows))
    config=Settings(data_dir=tmp_path/'app');target=config.evidence_dir/'labs/congress'
    import_archive(source,target,include_prices=False)
    data=LabData(target)
    assert data.rows(field='engine')['items'][0]['filedTimeZoneAssumption']=='America/New_York'
    # An old imported backup must recover the dropped field from full evidence.
    with sqlite3.connect(target/'congress.sqlite3') as db:
        projected=data.rows(field='engine')['items'][0];projected.pop('filedTimeZoneAssumption')
        db.execute('UPDATE records SET engine=?',(zlib.compress(json.dumps(projected).encode()),))
    manifest=load_json(target/'manifest.json');manifest.pop('engineProjectionVersion');atomic_json(target/'manifest.json',manifest)
    assert data.rows(field='engine')['items'][0]['filedTimeZoneAssumption']=='America/New_York'
    monkeypatch.setattr('backend.app.research.labs.load_ohlc',lambda symbol:{'bars':bars()})
    result=LabsService(config).backtest(dict(strategy='next_high',politician='all',chamber='all',start=DAYS[0],end=DAYS[-1],sizing='fixed_2',delay=1,add_purchases=True,short_holding=False,exit_price='next_low',fee_bps=10))
    assert result['executedBuys']==1
    assert result['entries'][0]['entryDate']=='2025-01-06'
    assert result['availabilityDiagnostics']['filingTimezoneAssumptions']==1
    assert any('timezone assumption' in note for note in result['assumptions'])
    assert not any(e.get('reason')=='Invalid public availability timestamp' for e in result['exclusions'])


def test_unknown_owner_filter_includes_missing_empty_and_explicit_unknown(tmp_path):
    source=tmp_path/'old'; folder=source/'public/data'; folder.mkdir(parents=True)
    rows=[row('missing',owner=None),row('empty',owner=''),row('blank',owner='  '),row('explicit',owner='Unknown'),row('self',owner='Self')]
    (folder/'records.json').write_text(json.dumps(rows))
    target=tmp_path/'imported'
    import_archive(source,target,include_prices=False)
    data=LabData(target)
    unknown=data.rows(owner='Unknown')
    assert unknown['total']==4
    assert {item['id'] for item in unknown['items']}=={'missing','empty','blank','explicit'}
    assert data.rows(owner='all')['total']==5
    assert [item['id'] for item in data.rows(owner='Self')['items']]==['self']
