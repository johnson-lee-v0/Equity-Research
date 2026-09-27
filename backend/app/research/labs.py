"""Congress research service shared by the single local application."""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .lab_data import LabData, RESEARCH, atomic_json, load_json, import_archive
from .lab_engine.backtest import Engine
from .lab_engine.ledger_pages import csv_bytes
from .lab_engine.price_history import ARCHIVE_ROOT, price_bars, reviewed_price_policies
from .lab_engine.reported_positions import reconstruct_positions
from .lab_engine.rotation_backtest import RotationEngine
from .lab_engine.senate_backtest import SenateEngine
from .lab_engine.senate_prices import load_ohlc
from .lab_engine.trade_entries import entry_ledger
from .lab_engine.research_trades import daily_trades

STRATEGIES = [
    {'id':'rotation_20','label':'20-position rotation','description':'Next-session high purchases; fund from oldest existing security at the open. At most 20 securities.'},
    {'id':'next_high','label':'Next-session high','description':'Shared cash portfolio with conservative daily-high entry and optional next-session low exits.'},
    {'id':'legacy_open','label':'Disclosure-delay open','description':'Opening-price replay after a 1 or 31 calendar-day disclosure delay; no forced rotation.'},
    {'id':'filing_high','label':'Filing-day high (conditional)','description':'Historical timing experiment; filing date alone does not prove the daily high was reachable.'},
    {'id':'next_high_below_low','label':'Next high below trade-date low','description':'Conditional historical price screen; known disclosed sales only by default.'},
]


class LabsService:
    def __init__(self, config):
        self.config = config
        self.root = Path(config.evidence_dir) / 'labs'
        self.data = LabData(self.root / 'congress')
        self.lock = threading.RLock()

    def import_data(self, refresh=False):
        with self.lock:
            import_archive(Path(self.config.project_root)/'exp/congress-lab', self.data.root, refresh=refresh)
            return self.catalog()

    def catalog(self):
        manifest = self.data.manifest()
        metadata = {k:v for k,v in manifest.items() if k not in ('politicians','sourceHashes')}
        return {'available':self.data.available, 'canImport':(Path(self.config.project_root)/'exp/congress-lab/public/data/records.json').is_file(), 'metadata':metadata, 'politicians':manifest.get('politicians',[]), 'strategies':STRATEGIES,
                'research':[{'id':key,'label':value,'available':(self.data.root/'research-snapshots'/f'{key}.json').is_file()} for key,value in RESEARCH.items()],
                'watch':self.watch(), 'owners':['Self','Spouse','Joint','Dependent','Unknown'],
                'methodNotes':['Dollar ranges describe disclosures, not actual shares or current portfolio weights.', 'Simulated purchases use one shared $100,000 account and a 10% ticker purchase cap.', 'Source snapshots and saved studies retain their original dates and coverage limits.']}

    def watch(self):
        # Existing external watcher owns cadence. This service neither starts a
        # second poller nor sends messages. Only public observation status is read.
        directory = Path(self.config.project_root) / 'exp/congress-lab/work/disclosure-alerts'
        latest = load_json(directory/'latest-run.json', {})
        policy = load_json(directory/'policy.json', {})
        selected = {k:latest.get(k) for k in ('observedAt','status','newEventCount','changedFilerCount','eventCounts','sourceGeneratedAt') if k in latest}
        return {'available':bool(latest), 'status':latest.get('status','not_connected'), 'collectionEnabled':bool(policy.get('collectionEnabled')), 'deliveryEnabled':False, 'executionEnabled':False, 'plannedRefreshTimes':policy.get('plannedRefreshTimes',[]), 'timezone':policy.get('timezone'), **selected, 'note':'Status of the existing disclosure collector; the research app does not run or send alerts.'}

    def records(self, **filters): return self.data.rows(**filters)

    def export_csv(self, **filters): return csv_bytes(self.data.rows(field='payload', **filters)['items'])

    def record(self, identifier):
        row = self.data.record(identifier)
        available = load_json(self.data.root/'metadata/disclosure-availability.json', {})
        row['availability'] = available.get('reports',{}).get(row.get('filingId'))
        return row

    def positions(self, *, offset=0, limit=40, status='all', **filters):
        rows = self.data.rows(field='payload', **filters)['items']
        day = filters.get('date') or self.data.manifest()['snapshotDate']
        result = reconstruct_positions(rows, day)
        positions = result['positions']
        if status != 'all': positions = [r for r in positions if r.get('status')==status]
        return {'items':positions[offset:offset+limit], 'total':len(positions), 'offset':offset, 'limit':limit, 'counts':result['counts'], 'methodNotes':result['limitations']}

    def research(self, identifier):
        if identifier not in RESEARCH: raise KeyError(identifier)
        path = self.data.root/'research-snapshots'/f'{identifier}.json'
        if not path.is_file(): raise ValueError('This saved research snapshot is not installed')
        original = load_json(path)
        candidates = original.get('cases') or original.get('scenarios') or original.get('selectedSummary') or original.get('results') or []
        views = []
        for i, case in enumerate(candidates):
            key = case.get('id') or case.get('name') or str(i)
            execution = case.get('execution')
            curve = case.get('curve') or case.get('daily') or original.get('selectedCurves', {}).get(execution) or []
            points = [{**point, 'benchmark':point.get('benchmark', point.get('buyHoldBenchmark'))} for point in curve]
            metrics = case.get('engineMetrics') or case.get('metrics') or {}
            if not metrics and points:
                metrics = {'endingValue':points[-1].get('nav'), 'totalReturn':points[-1]['nav']/100000-1, **case.get('summary',{})}
            views.append({'id':key + (':' + execution if execution else ''), 'label':key + (' · ' + execution if execution else ''),
                          'metrics':metrics, 'curve':points,
                          'periods':case.get('periods',{}), 'diagnostics':case.get('diagnostics') or case.get('cash') or case.get('summary') or {},
                          'tradeCaseId':key if identifier=='overnight-research' else None})
        notes = list(original.get('limitations') or original.get('methodNotes') or [])
        plan = original.get('plan') or {}
        if isinstance(plan, dict): notes.extend(plan.get('limitations', []))
        for block in (plan, original.get('executionAssumptions',{}), original.get('rules',{})):
            if not isinstance(block, dict): continue
            for key, value in block.items():
                if key in ('createdAt','limitations') or 'sha256' in key.casefold(): continue
                if isinstance(value, (str,int,float,bool)):
                    label = re.sub(r'(?<!^)(?=[A-Z])', ' ', key).replace('_',' ').capitalize()
                    notes.append(label + ': ' + str(value))
        section_keys = ('measurement','periods','coverage','sourceCoverage','definitions','selection','candidates','sensitivity','uncertainty','windowSummary','verification','integrity','robustness','secondary','eventReturns','provenance')
        sections = [{'title':re.sub(r'(?<!^)(?=[A-Z])',' ', key).capitalize(), 'data':original[key]} for key in section_keys if key in original]
        original['view'] = {'title':RESEARCH[identifier], 'summary':original.get('summary') or original.get('scope') or original.get('rule') or 'Saved retrospective research; assumptions and source dates are retained below.',
                            'asOf':original.get('asOf') or original.get('snapshotDate'), 'cases':views,
                            'notes':list(dict.fromkeys(str(note) for note in notes)), 'sections':sections,
                            'findings':original.get('currentTests',[]) + original.get('previousHypotheses',[])}
        return original

    def research_trades(self, case, day):
        return daily_trades({'case':[case], 'date':[day]}, self.data.root/'overnight-trades.sqlite', self.data.root/'research-snapshots/overnight-research.json')

    def history(self):
        index = load_json(self.root/'backtests/index.json', [])
        return {'items':index}

    def result(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier): raise KeyError(identifier)
        path = self.root/'backtests'/f'{identifier}.json'
        if not path.is_file(): raise KeyError(identifier)
        return load_json(path)

    def backtest(self, request):
        with self.lock:
            return self._backtest(request)

    def _backtest(self, request):
        body = dict(request)
        if body['strategy'] in ('rotation_20','legacy_open'):
            body['short_holding'] = False
        start, end = body['start'], body['end']
        cutoff = self.data.manifest().get('priceCutoff')
        if start > end: raise ValueError('Choose a start on or before the end date')
        if cutoff and end > cutoff: raise ValueError('Choose an end within the installed price cutoff '+cutoff)
        if body['strategy']=='rotation_20' and body['sizing']=='quarter_kelly': raise ValueError('Rotation supports fixed 1%, 2% or 5% sizing')
        rows = self.data.rows(field='engine', date=end, politician=body['politician'], chamber=body['chamber'])['items']
        if not rows: raise ValueError('No disclosed records match this selection and period')
        # Prefer actual publication timestamps when present. Daily-price replays
        # wait until the following session when intraday ordering is unknown.
        timezone_assumptions = 0
        for row in rows:
            stamp = row.get('firstPublicAt') or row.get('filedAt')
            if stamp:
                try:
                    public = datetime.fromisoformat(stamp.replace('Z','+00:00'))
                    if public.tzinfo is None:
                        zone = row.get('filedTimeZoneAssumption') if not row.get('firstPublicAt') else None
                        if not zone: raise ValueError('Public timestamps need a timezone')
                        public = public.replace(tzinfo=ZoneInfo(zone))
                        row['filedAt'] = public.isoformat()
                        timezone_assumptions += 1
                    row['filedDate'] = max(row['filedDate'], public.astimezone(ZoneInfo('America/New_York')).date().isoformat())
                except (TypeError, ValueError, KeyError):
                    row['eligible'] = False; row['exclusion'] = 'Invalid public availability timestamp'
        symbols = {r['priceSymbol'] for r in rows if r.get('eligible')} | {'SPY'}
        token = ARCHIVE_ROOT.set(self.data.root)
        try:
            policies = reviewed_price_policies()
            kwargs = {'start':start, 'end':end, 'sizing':body['sizing'], 'fee_bps':body['fee_bps']}
            if body['strategy']=='legacy_open':
                prices = {s:price_bars(s) for s in symbols}; prices = {s:p for s,p in prices.items() if p}
                if not prices.get('SPY'): raise ValueError('Audited SPY price calendar is unavailable')
                engine = Engine(rows, prices, policies)
                shadow = engine.simulate(rows, delay=body['delay'], sizing='fixed_2', end=end, fee_bps=body['fee_bps']) if body['sizing']=='quarter_kelly' else None
                result = engine.simulate(rows, delay=body['delay'], history=shadow['closed'] if shadow else None, **kwargs)
            else:
                histories = {s:load_ohlc(s) for s in symbols}
                if not histories['SPY']['bars']: raise ValueError('Audited SPY OHLC calendar is unavailable')
                if body['strategy']=='rotation_20':
                    engine = RotationEngine(rows, histories, policies)
                    result = engine.simulate_rotation(rows, add_purchases=body['add_purchases'], exit_price=body['exit_price'], **kwargs)
                else:
                    engine = SenateEngine(rows, histories, policies)
                    settings = {'strategy':body['strategy'], 'add_purchases':body['add_purchases'], 'short_holding':body.get('short_holding',False), 'exit_price':body['exit_price']}
                    shadow = engine.simulate_senate(rows, end=end, sizing='fixed_2', fee_bps=body['fee_bps'], **settings) if body['sizing']=='quarter_kelly' else None
                    result = engine.simulate_senate(rows, history=shadow['closed'] if shadow else None, **settings, **kwargs)
            result['entries'] = entry_ledger(result, {r['id']:r for r in rows}, engine.prices)
            result['priceCoverage'] = {'symbolsRequested':len(symbols)-1, 'symbolsWithPrices':sum(s in engine.prices for s in symbols-{'SPY'}), 'missingSymbols':sorted(s for s in symbols if s not in engine.prices)}
        finally:
            ARCHIVE_ROOT.reset(token)
        assumptions = ['Historical research simulation; no orders are submitted.', 'Single shared USD 100,000 starting cash account; synthetic adjusted total-return units.', f"Execution cost: {body['fee_bps']:g} basis points per side; no taxes, borrow, settlement restrictions or market-impact model.", 'Filing dates are proxies when verified publication timestamps are absent. Next-session entries avoid same-filing-day timing assumptions.', 'Unknown identities and missing audited prices are excluded; stale marks remain visible.', 'Benchmark is buy-and-hold SPY on the same covered sessions; performance is retrospective.']
        if timezone_assumptions:
            assumptions.append(f'{timezone_assumptions} filing timestamps use the timezone assumption explicitly recorded with their source (rather than a verified publication timezone).')
        if body['strategy']=='rotation_20': assumptions.append('Funding/replacement sales use the open; daily-high purchases may reuse the proceeds on the same day. At most 20 securities, selected by oldest executed signal.')
        if body['strategy']=='filing_high': assumptions.append('Conditional filing-day experiment: daily-high execution may precede publication; not a no-lookahead result.')
        if body.get('short_holding'): assumptions.append('Later-sale exclusion enabled: uses subsequent disclosures retrospectively; not a no-lookahead result.')
        effective_request = dict(body)
        if body['strategy']=='legacy_open':
            for unused in ('add_purchases','exit_price','short_holding'): effective_request.pop(unused, None)
            effective_rules = {'entry':'Session open after selected calendar-day disclosure delay', 'exit':'Session open after disclosed sale delay', 'repeatPurchases':True}
        else:
            effective_request.pop('delay', None)
            if body['strategy']=='rotation_20': effective_request.pop('short_holding', None)
            effective_rules = {'entry':'Daily high on the selected disclosure session', 'exit':'Hold unless rotation requires a funding sale' if body['exit_price']=='hold' else 'Next-session low after disclosed sale', 'repeatPurchases':body['add_purchases']}
        identifier = uuid.uuid4().hex
        result.update(id=identifier, created_at=datetime.now(timezone.utc).isoformat(), request=effective_request, effectiveRules=effective_rules, assumptions=assumptions, availabilityDiagnostics={'filingTimezoneAssumptions':timezone_assumptions},
                      sourceSnapshot={k:v for k,v in self.data.manifest().items() if k in ('snapshotDate','priceCutoff','records','imported_at')})
        result['exclusionCount'] = len(result.get('exclusions',[]))
        # Annualized metrics cannot meaningfully characterize fewer than two
        # observations. Do not turn a one-day move into a headline CAGR.
        if len(result['curve']) < 2:
            for metric in ('cagr','volatility','sharpe'): result['metrics'][metric] = None
        atomic_json(self.root/'backtests'/f'{identifier}.json', result)
        index = self.history()['items']
        index.insert(0, {k:result[k] for k in ('id','created_at','request','metrics','exclusionCount')})
        atomic_json(self.root/'backtests/index.json', index)
        return result
