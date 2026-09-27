"""Conditional Senate daily-extrema experiments; never orders or execution forecasts."""
import collections
import datetime as dt
import math
from .backtest import Engine, END, metrics, owner_key, kelly
from .senate_prices import price_join, session_for
from .cash_capacity import request_capacity, summarize_cash

STRATEGIES = {
    'filing_high': 'Filing day · daily high',
    'next_high': 'Filing + 1 session · daily high',
    'next_high_below_low': 'Filing + 1 high < trade-date low',
}


def position_key(row):
    return (row['politicianId'], owner_key(row), row.get('account') or 'Unknown account', row['priceSymbol'])


class SenateEngine(Engine):
    def __init__(self, rows, histories, policies=None):
        self.histories = histories
        prices = {s: h['bars'] for s, h in histories.items() if h['bars']}
        super().__init__(rows, prices, policies)
        self.joins = {r['id']: price_join(r, histories.get(r.get('priceSymbol'), {}),
                      self.calendar if r.get('assetClass') == 'crypto' else self.stock_calendar) for r in rows}

    def schedule_senate(self, rows, start, cutoff, strategy, short_holding, exit_price, data_cutoff=None):
        events = collections.defaultdict(list)
        excluded = []
        data_cutoff = data_cutoff or cutoff
        available = [r for r in rows if r.get('filedDate') and r['filedDate'] <= data_cutoff
                     and (not r.get('tradeDate') or r['tradeDate'] <= data_cutoff)]
        sales = collections.defaultdict(list)
        for r in available:
            if r['eligible'] and r['action'] == 'sell' and r.get('tradeDate'):
                sales[position_key(r)].append(r)
        for rs in sales.values():
            rs.sort(key=lambda r: (r['tradeDate'], r['filedDate'], r['id']))
        for r in available:
            reason = None
            if not r['eligible']:
                reason = r['exclusion'] or 'Source or security ineligible'
            elif not r.get('tradeDate') or r['tradeDate'] > r['filedDate']:
                reason = 'Source trade date unavailable or after filing'
            cal = self.calendar if r.get('assetClass') == 'crypto' else self.stock_calendar
            # All disclosed exits wait for the next session, independently of the entry rule.
            next_session = strategy != 'filing_high' or r['action'] == 'sell'
            day = session_for(cal, r.get('filedDate'), next_session)
            if reason:
                excluded.append({'id': r['id'], 'reason': reason})
                continue
            if r['action'] == 'sell' and exit_price == 'hold':
                excluded.append({'id': r['id'], 'reason': 'Hold mode ignores disclosed sales'})
                continue
            if day is None or day > cutoff:
                excluded.append({'id': r['id'], 'reason': 'Execution after price cutoff'})
                continue
            if day < start:
                continue
            bar = self.prices.get(r['priceSymbol'], {}).get(day)
            if not bar:
                excluded.append({'id': r['id'], 'reason': 'Exact execution-session OHLC unavailable', 'date': day})
                continue
            detail = {'id': r['id'], 'date': day}
            if r['action'] == 'buy':
                matches = [s for s in sales.get(position_key(r), []) if r['tradeDate'] <= s['tradeDate'] <= day]
                known = [s for s in matches if s['filedDate'] < day]
                veto = matches if short_holding else known
                if veto:
                    excluded.append({**detail, 'reason': 'Disclosed sale on/before follower entry',
                                     'saleSourceIds': [s['id'] for s in veto],
                                     'usesLaterDisclosure': not bool(known),
                                     'holdingGapDays': (dt.date.fromisoformat(day) - dt.date.fromisoformat(r['tradeDate'])).days,
                                     'sameDaySequenceUnknown': any(s['tradeDate'] == r['tradeDate'] for s in veto),
                                     'unknownAccountBucketAssumed': not bool(r.get('account')),
                                     'partialSaleTreatedAsExit': all('partial' in str(s.get('transactionType')).lower() for s in veto)})
                    continue
                if strategy == 'next_high_below_low':
                    passed = self.joins[r['id']]['nextHighBelowTradeLow']
                    if passed is not True:
                        excluded.append({**detail, 'reason': 'Trade-date low unavailable' if passed is None
                                         else 'Next-session high is not strictly below trade-date low'})
                        continue
            events[day].append(r)
        return events, excluded

    def simulate_senate(self, rows, start='2020-01-02', end=END, strategy='next_high',
                        sizing='fixed_2', add_purchases=True, short_holding=True,
                        exit_price='next_low', history=None, fee_bps=10):
        cost_rate = fee_bps / 10000
        if strategy not in STRATEGIES or exit_price not in ('hold', 'next_low'):
            raise ValueError('Invalid Senate strategy')
        if sizing not in ('fixed_1', 'fixed_2', 'fixed_5', 'quarter_kelly'):
            raise ValueError('Invalid sizing')
        cutoff = end
        calendar = [d for d in self.calendar if start <= d <= cutoff]
        self.joins = {r['id']: price_join(r, self.histories.get(r.get('priceSymbol'), {}),
                      self.calendar if r.get('assetClass') == 'crypto' else self.stock_calendar, cutoff) for r in rows}
        events, excluded = self.schedule_senate(rows, start, cutoff, strategy, short_holding, exit_price, end)
        lots = []
        orders, closed, curve = [], [], []
        cash = 100000.
        fees = turnover = 0.
        unmatched = scaled = skipped = added = 0
        monthly = {}
        first_stock_day = next((d for d in self.stock_calendar if d >= start), None)
        sp0 = self.prices['SPY'][first_stock_day]['open'] if first_stock_day else 1.

        def mark(lot, day, previous=False):
            marks = self.previous_close if previous else self.close
            return marks.get(lot['symbol'], {}).get(day, lot['entryPrice'])

        for day in calendar:
            # Daily extrema do not reveal intraday order. Sale proceeds fund buys only tomorrow.
            opening_cash = cash
            nav_before = cash + sum(p['units'] * mark(p, day, True) for p in lots)
            today = sorted(events.get(day, []), key=lambda r: (r['tradeDate'], r['id']))
            for r in [r for r in today if r['action'] == 'sell']:
                matched = [p for p in lots if p['key'] == position_key(r) and p['tradeDate'] <= r['tradeDate']]
                if not matched:
                    unmatched += 1
                    orders.append({'id': r['id'], 'date': day, 'action': 'sell', 'status': 'No earlier matching copied purchase', 'notional': 0})
                    continue
                field = 'low'
                price = self.prices[r['priceSymbol']][day][field]
                gross = sum(p['units'] for p in matched) * price
                fee = gross * cost_rate
                cost = sum(p['cost'] for p in matched)
                cash += gross - fee
                fees += fee
                turnover += gross
                source_ids = [p['sourceId'] for p in matched]
                outcome = {'date': day, 'entryDate': min(p['firstDate'] for p in matched),
                           'politicianId': r['politicianId'], 'ticker': r['ticker'], 'owner': r['owner'],
                           'account': r.get('account'), 'return': (gross - fee) / cost - 1,
                           'pnl': gross - fee - cost, 'cost': cost, 'exitId': r['id'], 'purchaseSourceIds': source_ids}
                closed.append(outcome)
                orders.append({'id': r['id'], 'date': day, 'action': 'sell', 'status': 'Executed: exit matching earlier lots',
                               'notional': gross, 'fee': fee, 'executionPrice': price, 'priceField': field,
                               'quotePrice': self.prices[r['priceSymbol']][day]['quote'][field],
                               'return': outcome['return'], 'purchaseSourceIds': source_ids})
                matched_ids = set(source_ids)
                lots = [p for p in lots if p['sourceId'] not in matched_ids]
            if sizing == 'quarter_kelly':
                if day[:7] not in monthly:
                    monthly[day[:7]] = kelly(history or [], day)
                diagnostic = monthly[day[:7]]
                fraction = min(.05, .25 * diagnostic['fraction']) if diagnostic['fraction'] is not None else .01
            else:
                diagnostic = None
                fraction = {'fixed_1': .01, 'fixed_2': .02, 'fixed_5': .05}[sizing]
            buys = []
            occupied = {p['key'] for p in lots}
            for r in [r for r in today if r['action'] == 'buy']:
                if not add_purchases and position_key(r) in occupied:
                    skipped += 1
                    excluded.append({'id': r['id'], 'date': day, 'reason': 'Repeated purchases disabled while this position is open'})
                    continue
                buys.append(r)
                occupied.add(position_key(r))
            counts = collections.Counter(r['priceSymbol'] for r in buys)
            current = collections.defaultdict(float)
            for p in lots:
                current[p['symbol']] += p['units'] * mark(p, day, True)
            requests = [(r, min(nav_before * fraction, max(0, nav_before * .10 - current[r['priceSymbol']]) / counts[r['priceSymbol']])) for r in buys]
            total = sum(wanted for _, wanted in requests) * (1 + cost_rate)
            scale = min(1., opening_cash / total) if total else 0.
            for r, wanted in requests:
                gross = wanted * scale
                capacity = request_capacity(nav_before * fraction, wanted, gross, opening_cash, scale)
                if gross < 1:
                    skipped += 1
                    orders.append({'id': r['id'], 'date': day, 'action': 'buy', 'status': 'No cash, no Kelly edge or ticker cap', 'notional': 0, 'capacity': capacity})
                    continue
                is_add = any(p['key'] == position_key(r) for p in lots)
                added += int(is_add)
                fee = gross * cost_rate
                price = self.prices[r['priceSymbol']][day]['high']
                cash -= gross + fee
                fees += fee
                turnover += gross
                lots.append({'key': position_key(r), 'symbol': r['priceSymbol'], 'ticker': r['ticker'],
                             'politicianId': r['politicianId'], 'politician': r['politician'], 'asset': r['asset'],
                             'owner': r['owner'], 'dependentChildId': r.get('dependentChildId'), 'account': r.get('account'),
                             'units': gross / price, 'cost': gross + fee, 'entryPrice': price,
                             'firstDate': day, 'tradeDate': r['tradeDate'], 'sourceId': r['id']})
                was_scaled = scale < .999999 or wanted < nav_before * fraction - .01
                scaled += int(was_scaled)
                orders.append({'id': r['id'], 'date': day, 'action': 'buy', 'status': ('Added to position' if is_add else 'Executed') + (' · scaled to cash / cap' if was_scaled else ''),
                               'notional': gross, 'fee': fee, 'executionPrice': price, 'quotePrice': self.prices[r['priceSymbol']][day]['quote']['high'],
                               'priceField': 'high', 'addedToPosition': is_add, 'targetWeight': fraction, 'capacity': capacity,
                               'navBefore': nav_before, 'kelly': diagnostic, 'priceJoin': self.joins[r['id']]})
            assert cash >= -.0001 and all(p['units'] > 0 for p in lots)
            value = sum(p['units'] * mark(p, day) for p in lots)
            nav = cash + value
            assert math.isfinite(nav) and nav > 0
            benchmark = 100000 * self.close['SPY'][day] / sp0 if day >= (first_stock_day or '9999') else 100000.
            curve.append({'date': day, 'nav': round(nav, 4), 'cash': round(cash, 4), 'invested': value / nav, 'benchmark': round(benchmark, 4)})
        if not curve:
            curve = [{'date': cutoff, 'nav': 100000., 'cash': 100000., 'invested': 0., 'benchmark': 100000.}]
        groups = collections.defaultdict(list)
        for p in lots:
            groups[p['key']].append(p)
        holdings = []
        for key, ps in groups.items():
            first = ps[0]
            symbol = first['symbol']
            price = mark(first, curve[-1]['date'])
            last = self.lastdate[symbol].get(curve[-1]['date'])
            policy = self.price_policies.get(symbol, {})
            corporate = bool(policy.get('lastTradableSession') and curve[-1]['date'] > policy['lastTradableSession'])
            stale = not last or (dt.date.fromisoformat(curve[-1]['date']) - dt.date.fromisoformat(last)).days > 7 or corporate
            units, cost = sum(p['units'] for p in ps), sum(p['cost'] for p in ps)
            holdings.append({**{k: first.get(k) for k in ('symbol', 'ticker', 'politicianId', 'politician', 'asset', 'owner', 'dependentChildId', 'account')},
                             'id': repr(key), 'units': units, 'cost': cost, 'value': units * price, 'pnl': units * price - cost,
                             'entryPrice': cost / units, 'firstDate': min(p['firstDate'] for p in ps),
                             'sourceIds': [p['sourceId'] for p in ps], 'purchaseLots': len(ps), 'lastPriceDate': last,
                             'stale': stale, 'valuationRequiresCorporateAction': corporate,
                             'status': 'stale_mark' if stale else 'simulated_open', 'quantityType': 'synthetic_total_return_units'})
        result = {'metrics': metrics(curve, annual_factor=365.25 if self.crypto else 252), 'curve': curve,
                'orders': orders, 'closed': closed, 'holdings': sorted(holdings, key=lambda p: -p['value']),
                'exclusions': excluded, 'exclusionCount': len(excluded), 'recordCount': len(rows),
                'fees': fees, 'turnover': turnover / 100000, 'executedBuys': sum(o['action'] == 'buy' and o['notional'] > 0 for o in orders),
                'additionalPurchases': added, 'unmatchedSales': unmatched, 'scaledBuys': scaled, 'skippedBuys': skipped,
                'staleValue': sum(p['value'] for p in holdings if p['stale']),
                'valuationStatus': 'incomplete' if any(p['stale'] for p in holdings) else 'current_marks',
                'kellyNow': kelly(history or closed, end),
                'strategy': strategy, 'strategyLabel': STRATEGIES[strategy], 'addPurchases': add_purchases,
                'shortHoldingFilter': short_holding, 'exitPrice': exit_price,
                'shortHoldingExclusions': sum(e['reason'] == 'Disclosed sale on/before follower entry' for e in excluded),
                'laterDisclosureExclusions': sum(e.get('usesLaterDisclosure', False) for e in excluded),
                'conditionalResearch': True,
                'methodNotes': [
                    'Senate only. Source eligibility, identity, corrections and missing-price exclusions are retained.',
                    'Filing date is a public-availability proxy. Filing day rolls forward on holidays/weekends; +1 is the strictly next session after filing date. Crypto uses UTC calendar days.',
                    'Buy at the daily high. The below-low screen uses the complete entry-day high and is a retrospective condition, not an intraday executable signal.',
                    'Price comparison uses vendor quote OHLC on a common historical split basis, without dividend adjustments. Returns use adjusted total-return units.',
                    'The short-holding filter excludes a matched sale dated on/before entry, including later disclosures through the selected cutoff. Disabling it still excludes sales already filed by entry date.',
                    'Match politician, owner, child identity, source account and security. Unreported accounts share an assumed bucket; these are inferred matches, not verified investor lots.',
                    'Exit toggle: hold through the cutoff, or sell at the next session daily low after a sale is filed. Any matching sale, including partial, closes earlier copied lots. It cannot close purchases with a later source trade date. Actual quantities and lot matching are unknown.',
                    'Sizing uses previous-close NAV, one $100,000 cash account, 10% ticker purchase cap, 10bps per side. Sale proceeds are available to buy on the following day.',
                    'Unclosed positions are marked through the cutoff. Missing or stale valuations remain flagged. No source prices, shares or opening balances are invented.'
                ]}
        result['cashDiagnostics'] = summarize_cash(result)
        return result
