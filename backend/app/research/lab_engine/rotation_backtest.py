"""Twenty-security rotation with explicit funding sales and persistent entry requests.

An execution model, not an alpha claim: no return ranking or future membership is
used to select an exit. Existing uncapped replay engines remain unchanged.
"""
import collections
import datetime as dt
import math

from .backtest import END, metrics, kelly
from .cash_capacity import request_capacity, summarize_cash
from .senate_backtest import SenateEngine, position_key

MAX_POSITIONS = 20
LABEL = '20 positions · oldest signal replaced'


class RotationEngine(SenateEngine):
    def simulate_rotation(self, rows, start='2020-01-02', end=END,
                          sizing='fixed_2', add_purchases=True, exit_price='next_low', fee_bps=10):
        if not 0 <= fee_bps <= 100: raise ValueError('Invalid fee rate')
        cost_rate = fee_bps / 10000
        if sizing not in ('fixed_1', 'fixed_2', 'fixed_5'):
            raise ValueError('Rotation supports fixed 1%, 2% or 5% purchase sizing')
        if exit_price not in ('hold', 'next_low'):
            raise ValueError('Invalid rotation exit rule')
        fraction = {'fixed_1': .01, 'fixed_2': .02, 'fixed_5': .05}[sizing]
        cutoff = end
        calendar = [d for d in self.calendar if start <= d <= cutoff]
        # Known-sale veto only. The below-low hindsight screen is not used.
        invalid_sales = {r['id'] for r in rows if r['action'] == 'sell' and r.get('tradeDate') and r.get('filedDate') and r['tradeDate'] > r['filedDate']}
        events, excluded = self.schedule_senate([r for r in rows if r['id'] not in invalid_sales], start, cutoff, 'next_high', False, exit_price, end)
        excluded += [{'id': rid, 'reason': 'Source trade date after filing'} for rid in sorted(invalid_sales)]
        known_sales = collections.defaultdict(list)
        for r in rows:
            if r['eligible'] and r['action'] == 'sell' and r.get('filedDate') and r.get('tradeDate') and r['tradeDate'] <= r['filedDate']:
                known_sales[position_key(r)].append(r)
        lots, orders, closed, curve, pending = [], [], [], [], []
        cash = 100000.
        fees = turnover = 0.
        unmatched = added = scaled = skipped = peak = 0
        deferrals = collections.Counter()
        first_stock = next((d for d in self.stock_calendar if start <= d <= cutoff), None)
        sp0 = self.prices['SPY'][first_stock]['open'] if first_stock else 1.

        def mark(p, day, previous=False):
            return (self.previous_close if previous else self.close).get(p['symbol'], {}).get(day, p['entryPrice'])

        def sell(matched, day, field, source=None, trigger=None, reason=None):
            nonlocal cash, fees, turnover, lots
            symbol = matched[0]['symbol']
            bar = self.prices[symbol][day]
            price = bar[field]
            gross = sum(p['units'] for p in matched) * price
            fee = gross * cost_rate
            cash += gross - fee
            fees += fee
            turnover += gross
            ids = [p['sourceId'] for p in matched]
            oid = source['id'] if source else f"rotation:{day}:{len(orders)}:{symbol}"
            # Retain one outcome per source lot so politician attribution and P&L
            # remain valid when one security contains several owners or people.
            for p in matched:
                proceeds = p['units'] * price * (1 - cost_rate)
                closed.append({'date': day, 'entryDate': p['firstDate'],
                    'politicianId': p['politicianId'], 'ticker': p['ticker'],
                    'owner': p['owner'], 'account': p['account'], 'cost': p['cost'],
                    'return': proceeds / p['cost'] - 1, 'pnl': proceeds - p['cost'],
                    'exitId': oid, 'purchaseSourceIds': [p['sourceId']], 'exitReason': reason or 'disclosed_sale'})
            orders.append({'id': oid, 'date': day, 'action': 'sell', 'symbol': symbol,
                'status': 'Rotation: ' + reason if reason else 'Executed: exit matching earlier lots',
                'notional': gross, 'fee': fee, 'executionPrice': price,
                'quotePrice': bar['quote'][field], 'priceField': field,
                'purchaseSourceIds': ids, 'synthetic': source is None,
                'triggerSourceId': trigger, 'exitReason': reason or 'disclosed_sale'})
            removed = set(ids)
            lots = [p for p in lots if p['sourceId'] not in removed]

        for day in calendar:
            opening_cash = cash
            nav_before = cash + sum(p['units'] * mark(p, day, True) for p in lots)
            touched = set()
            today = sorted(events.get(day, []), key=lambda r: (r['tradeDate'], r['id']))
            pending += [{'row': r, 'scheduledDate': day, 'firstDeferredDate': None, 'attempts': 0}
                        for r in today if r['action'] == 'buy']
            pending.sort(key=lambda p: (p['scheduledDate'], p['row']['tradeDate'], p['row']['id']))
            remaining = []
            for request in pending:
                r = request['row']; symbol = r['priceSymbol']
                if r.get('assetClass') != 'crypto' and day not in self.stock_calendar:
                    remaining.append(request); continue
                veto = [s['id'] for s in known_sales[position_key(r)]
                        if s['filedDate'] < day and r['tradeDate'] <= s['tradeDate'] <= day]
                if veto:
                    excluded.append({'id': r['id'], 'date': day,
                        'reason': 'Known sale before deferred entry', 'saleSourceIds': veto})
                    continue
                if not add_purchases and any(p['key'] == position_key(r) for p in lots):
                    excluded.append({'id': r['id'], 'date': day, 'reason': 'Repeated purchases disabled while this position is open'})
                    continue
                bar = self.prices.get(symbol, {}).get(day)
                reason = None
                if not bar:
                    reason = 'Entry price unavailable'
                groups = collections.defaultdict(list)
                for p in lots: groups[p['symbol']].append(p)
                current = sum(p['units'] * (p['entryPrice'] if p['firstDate'] == day else mark(p, day, True))
                              for p in groups[symbol])
                target = nav_before * fraction
                wanted = min(target, max(0., nav_before * .10 - current))
                if wanted < 1:
                    skipped += 1
                    orders.append({'id': r['id'], 'date': day, 'action': 'buy',
                        'status': 'Ticker cap or minimum purchase size', 'notional': 0,
                        'capacity': request_capacity(target, wanted, 0, cash, 0)})
                    continue
                needs_slot = symbol not in {p['symbol'] for p in lots} and len({p['symbol'] for p in lots}) >= MAX_POSITIONS
                # Plan atomically before selling. A symbol bought today cannot be
                # sold at that day's earlier open, including an existing top-up.
                candidates = sorted((s for s in groups if groups[s] and s != symbol and s not in touched
                                     and day in self.prices.get(s, {})),
                                    key=lambda s: (max(p['firstDate'] for p in groups[s]), s))
                planned = []; budget = cash
                for victim in candidates:
                    if not needs_slot and budget >= wanted * (1 + cost_rate) - 1e-8: break
                    planned.append(victim)
                    budget += sum(p['units'] for p in groups[victim]) * self.prices[victim][day]['open'] * (1 - cost_rate)
                    needs_slot = False
                if needs_slot or budget < wanted * (1 + cost_rate) - 1e-8:
                    reason = reason or 'Waiting for a tradable opening position to fund entry'
                if reason:
                    request['firstDeferredDate'] = request['firstDeferredDate'] or day
                    request['attempts'] += 1; request['reason'] = reason
                    deferrals[r['id']] += 1
                    remaining.append(request)
                    continue
                for victim in planned:
                    sell(groups[victim], day, 'open', trigger=r['id'],
                         reason='position_limit' if len({p['symbol'] for p in lots}) >= MAX_POSITIONS and symbol not in {p['symbol'] for p in lots} else 'cash_funding')
                budget = cash
                gross = wanted
                fee = gross * cost_rate
                cash -= gross + fee
                if -1e-7 < cash < 0: cash = 0.
                fees += fee; turnover += gross
                is_add = symbol in {p['symbol'] for p in lots}
                added += int(is_add); scaled += int(wanted < target - .01)
                price = bar['high']
                lots.append({'key': position_key(r), 'symbol': symbol, 'ticker': r['ticker'],
                    'politicianId': r['politicianId'], 'politician': r['politician'], 'asset': r['asset'],
                    'owner': r['owner'], 'dependentChildId': r.get('dependentChildId'), 'account': r.get('account'),
                    'units': gross / price, 'cost': gross + fee, 'entryPrice': price,
                    'firstDate': day, 'tradeDate': r['tradeDate'], 'sourceId': r['id']})
                touched.add(symbol)
                count = len({p['symbol'] for p in lots}); peak = max(peak, count)
                assert count <= MAX_POSITIONS and cash >= -1e-7
                orders.append({'id': r['id'], 'date': day, 'action': 'buy', 'status': 'Added to position' if is_add else 'Executed',
                    'symbol': symbol, 'notional': gross, 'fee': fee, 'executionPrice': price,
                    'quotePrice': bar['quote']['high'], 'priceField': 'high', 'addedToPosition': is_add,
                    'targetWeight': fraction, 'navBefore': nav_before,
                    'scheduledDate': request['scheduledDate'], 'deferred': day != request['scheduledDate'],
                    'replacedSymbols': planned, 'openPositionCount': count,
                    'capacity': request_capacity(target, wanted, gross, budget, 1.)})
            pending = remaining
            # Daily-low disclosed exits cannot fund today's entries. They also
            # cannot sell units that had already been removed by a rotation.
            for r in (r for r in today if r['action'] == 'sell'):
                matched = [p for p in lots if p['key'] == position_key(r) and p['tradeDate'] <= r['tradeDate']]
                if matched: sell(matched, day, 'low', source=r)
                else:
                    unmatched += 1
                    orders.append({'id': r['id'], 'date': day, 'action': 'sell', 'status': 'No earlier matching copied purchase', 'notional': 0})
            value = sum(p['units'] * mark(p, day) for p in lots); nav = cash + value
            assert math.isfinite(nav) and nav > 0 and cash >= -1e-7
            curve.append({'date': day, 'nav': round(nav, 4), 'cash': round(cash, 4), 'invested': value / nav,
                'benchmark': round(100000 * self.close['SPY'][day] / sp0, 4) if first_stock and day >= first_stock else 100000.,
                'openingCash': opening_cash, 'positions': len({p['symbol'] for p in lots}), 'pending': len(pending)})
        if not curve: curve = [{'date': cutoff, 'nav': 100000., 'cash': 100000., 'invested': 0., 'benchmark': 100000., 'positions': 0, 'pending': 0}]
        groups = collections.defaultdict(list)
        for p in lots: groups[p['key']].append(p)
        holdings = []
        for key, ps in groups.items():
            first = ps[0]; symbol = first['symbol']; day = curve[-1]['date']
            price = mark(first, day); last = self.lastdate[symbol].get(day)
            policy = self.price_policies.get(symbol, {})
            corporate = bool(policy.get('lastTradableSession') and day > policy['lastTradableSession'])
            stale = not last or (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days > 7 or corporate
            units = sum(p['units'] for p in ps); cost = sum(p['cost'] for p in ps)
            holdings.append({**{k: first.get(k) for k in ('symbol', 'ticker', 'politicianId', 'politician', 'asset', 'owner', 'dependentChildId', 'account')},
                'id': repr(key), 'units': units, 'cost': cost, 'value': units * price, 'pnl': units * price - cost,
                'entryPrice': cost / units, 'firstDate': min(p['firstDate'] for p in ps),
                'sourceIds': [p['sourceId'] for p in ps], 'purchaseLots': len(ps), 'lastPriceDate': last,
                'stale': stale, 'valuationRequiresCorporateAction': corporate,
                'status': 'stale_mark' if stale else 'simulated_open', 'quantityType': 'synthetic_total_return_units'})
        rotations = [o for o in orders if o.get('synthetic')]
        buys = [o for o in orders if o['action'] == 'buy' and o['notional'] > 0]
        pending_summary = [{'id': p['row']['id'], 'symbol': p['row']['priceSymbol'],
            'scheduledDate': p['scheduledDate'], 'firstDeferredDate': p['firstDeferredDate'],
            'attempts': p['attempts'], 'reason': p.get('reason', 'Next market session')} for p in pending]
        result = {'strategy': 'rotation_20', 'strategyLabel': LABEL, 'metrics': metrics(curve, annual_factor=365.25 if self.crypto else 252),
            'curve': curve, 'orders': orders, 'closed': closed, 'holdings': sorted(holdings, key=lambda p: -p['value']),
            'exclusions': excluded, 'exclusionCount': len(excluded), 'recordCount': len(rows),
            'feeBpsPerSide': fee_bps, 'fees': fees, 'turnover': turnover / 100000, 'executedBuys': len(buys), 'additionalPurchases': added,
            'unmatchedSales': unmatched, 'scaledBuys': scaled, 'skippedBuys': skipped,
            'staleValue': sum(p['value'] for p in holdings if p['stale']),
            'valuationStatus': 'incomplete' if any(p['stale'] for p in holdings) else 'current_marks',
            'kellyNow': kelly(closed, end), 'addPurchases': add_purchases, 'shortHoldingFilter': False,
            'exitPrice': exit_price, 'shortHoldingExclusions': sum(e['reason'] in ('Disclosed sale on/before follower entry', 'Known sale before deferred entry') for e in excluded),
            'laterDisclosureExclusions': 0, 'conditionalResearch': True,
            'rotation': {'maxPositions': MAX_POSITIONS, 'peakPositions': peak, 'endingPositions': len({p['symbol'] for p in lots}),
                'replacementRule': 'Oldest most recent executed purchase; ticker breaks ties',
                'forcedExits': len(rotations), 'slotExits': sum(o['exitReason'] == 'position_limit' for o in rotations),
                'fundingExits': sum(o['exitReason'] == 'cash_funding' for o in rotations),
                'netFundingProceeds': sum(o['notional'] - o['fee'] for o in rotations),
                'deferredRequests': len(deferrals), 'deferredExecuted': sum(o['deferred'] for o in buys),
                'pendingCount': len(pending), 'pending': pending_summary},
            'methodNotes': [
                'At most 20 unique resolved price symbols, across all selected politicians and owners. Lots retain source attribution.',
                'Replace the holding with the oldest most recent executed purchase; ticker breaks ties. No future return ranking.',
                f'Funding exits sell all lots of a security at the session open, with {fee_bps}bps cost. Entry uses that session daily high as an adverse bound, after opening funding sales. This requires a broker/account that permits reuse of unsettled sale proceeds.',
                'Never sell a security at the earlier open after purchasing or adding to it that day. Overflow requests remain queued, oldest scheduled request first, and known sale disclosures are checked again before entry.',
                'Fixed purchase target uses previous-close NAV; 10% ticker purchase cap and $1 minimum remain. Cash shortages trigger sales even below 20 positions. Missing prices defer funding rather than invent proceeds.',
                'Hold ignores politician sale signals; mandatory rotation exits still apply. Disclosed-sale exits use next-session low and only fund following-day purchases.',
                'Filing dates proxy public availability. No next-day-high-below-low screen or later-disclosure entry veto. Unfilled pending requests are reported at the cutoff.',
            ]}
        result['cashDiagnostics'] = summarize_cash(result)
        assert result['cashDiagnostics']['cashSkipped'] == 0
        return result
