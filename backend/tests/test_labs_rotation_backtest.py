import copy
import random
import unittest
from backend.app.research.lab_engine.rotation_backtest import RotationEngine
from backend.app.research.lab_engine.trade_entries import entry_ledger
from backend.tests.lab_fixtures import row, bars, DAYS


def replay(rows, histories=None, **settings):
    histories = histories or {s: {'bars': bars()} for s in {r['priceSymbol'] for r in rows} | {'SPY'}}
    engine = RotationEngine(rows, histories)
    result = engine.simulate_rotation(rows, start=DAYS[0], end=settings.pop('end', DAYS[-1]), **settings)
    result['entries'] = entry_ledger(result, {r['id']: r for r in rows}, engine.prices)
    return result


def seed(n=20):
    return [row(f'b{i:02}', priceSymbol=f'S{i:02}', ticker=f'S{i:02}') for i in range(n)]


class RotationTest(unittest.TestCase):
    def test_invalid_sale_chronology_cannot_veto_purchase(self):
        rs = [row('buy'), row('bad-sale', action='sell', tradeDate='2025-01-04', filedDate='2025-01-03')]
        p = replay(rs)
        self.assertEqual(p['executedBuys'], 1)
        self.assertTrue(any(e['id'] == 'bad-sale' and e['reason'] == 'Source trade date after filing' for e in p['exclusions']))
        self.assert_accounting(p)

    def assert_accounting(self, result):
        entries = result['entries']; nav = result['metrics']['endingValue']
        self.assertAlmostEqual(100000 + sum(e['pnl'] for e in entries), nav, places=3)
        self.assertAlmostEqual(result['curve'][-1]['cash'] + sum(e['value'] for e in entries if e['status'] == 'open'), nav, places=3)
        self.assertAlmostEqual(result['fees'], sum(e['entryFee'] + e['exitFee'] for e in entries))
        self.assertAlmostEqual(sum(e['pnl'] for e in entries if e['status'] == 'closed'), sum(c['pnl'] for c in result['closed']))
        live = {}; cash = 100000.; byid = {e['id']: e for e in entries}
        touched = collections.defaultdict(set)
        for o in result['orders']:
            if o['notional'] <= 0: continue
            if o['action'] == 'buy':
                live[o['id']] = byid[o['id']]['symbol']
                touched[o['date']].add(live[o['id']])
                cash -= o['notional'] + o['fee']
            else:
                if o.get('synthetic'):
                    self.assertNotIn(o['symbol'], touched[o['date']])
                for source in o['purchaseSourceIds']: del live[source]
                cash += o['notional'] - o['fee']
            self.assertLessEqual(len(set(live.values())), 20)
            self.assertGreaterEqual(cash, -1e-7)
        self.assertEqual(result['cashDiagnostics']['cashSkipped'], 0)

    def test_twenty_first_replaces_oldest_even_with_cash(self):
        rs = seed() + [row('new', priceSymbol='NEW', ticker='NEW', filedDate='2025-01-06')]
        p = replay(rs)
        sale = next(o for o in p['orders'] if o.get('synthetic'))
        self.assertEqual((sale['symbol'], sale['priceField'], sale['exitReason']), ('S00', 'open', 'position_limit'))
        self.assertEqual(p['rotation']['peakPositions'], 20)
        self.assertEqual(p['rotation']['pendingCount'], 0)
        self.assert_accounting(p)

    def test_large_same_day_batch_queues_and_resumes(self):
        rs = seed(45); p = replay(rs)
        self.assertEqual(p['executedBuys'], 45)
        self.assertEqual(p['rotation']['deferredExecuted'], 25)
        self.assertEqual(p['rotation']['pendingCount'], 0)
        self.assertGreater(max(c['pending'] for c in p['curve']), 0)
        self.assert_accounting(p)
        random.Random(9).shuffle(rs)
        self.assertEqual(p['orders'], replay(rs)['orders'])

    def test_same_security_owners_members_aggregate_and_close(self):
        rs = seed() + [row('same-spouse', priceSymbol='S00', owner='Spouse'),
                       row('same-person', priceSymbol='S00', politicianId='senate_q'),
                       row('new', priceSymbol='NEW', filedDate='2025-01-06')]
        p = replay(rs)
        sale = next(o for o in p['orders'] if o.get('synthetic'))
        self.assertEqual(set(sale['purchaseSourceIds']), {'b00', 'same-spouse', 'same-person'})
        self.assertEqual(len([e for e in p['entries'] if e['symbol'] == 'S00' and e['status'] == 'closed']), 3)
        self.assertTrue(all(not any(i.startswith('rotation:') for i in e['sourceIds']) for e in p['entries']))
        self.assertEqual({c['politicianId'] for c in p['closed']}, {'senate_p', 'senate_q'})
        self.assert_accounting(p)

    def test_topup_protected_from_earlier_open_sale(self):
        rs = seed() + [row('a-topup', priceSymbol='S00', filedDate='2025-01-06'),
                       row('z-new', priceSymbol='NEW', filedDate='2025-01-06')]
        p = replay(rs)
        self.assertEqual(next(o for o in p['orders'] if o.get('synthetic'))['symbol'], 'S01')
        self.assert_accounting(p)

    def test_cash_funding_below_twenty(self):
        rs = seed(15)
        rs += [row(f'add{i:02}', priceSymbol=f'S{i:02}', filedDate='2025-01-06') for i in range(15)]
        rs += [row('last', priceSymbol='NEW', filedDate='2025-01-07')]
        p = replay(rs, sizing='fixed_5')
        self.assertGreater(p['rotation']['fundingExits'], 0)
        self.assertEqual(p['rotation']['pendingCount'], 0)
        self.assert_accounting(p)

    def test_missing_funding_prices_keeps_request_pending(self):
        rs = seed() + [row('new', priceSymbol='NEW', filedDate='2025-01-06')]
        hs = {s: {'bars': bars()} for s in {r['priceSymbol'] for r in rs} | {'SPY'}}
        for s in [f'S{i:02}' for i in range(20)]:
            hs[s]['bars'] = {d: b for d, b in hs[s]['bars'].items() if d <= '2025-01-06'}
        p = replay(rs, hs)
        self.assertEqual(p['rotation']['pendingCount'], 1)
        self.assertEqual(p['rotation']['forcedExits'], 0)
        self.assertEqual(p['rotation']['pending'][0]['id'], 'new')
        self.assert_accounting(p)

    def test_deferred_known_sale_veto_no_future_sale(self):
        # A date-only filing is known at the following opening session.
        rs = seed(21) + [row('sale', priceSymbol='S20', action='sell', tradeDate='2025-01-06', filedDate='2025-01-06')]
        p = replay(rs, exit_price='hold')
        self.assertEqual(p['executedBuys'], 20)
        self.assertTrue(any(e['id'] == 'b20' and e['reason'] == 'Known sale before deferred entry' for e in p['exclusions']))
        rs[-1]['filedDate'] = '2025-01-13'
        p = replay(rs, exit_price='hold')
        self.assertEqual(p['executedBuys'], 21)
        self.assert_accounting(p)

    def test_hold_still_rotates_and_disclosed_exit_does_not_double_sell(self):
        rs = seed() + [row('new', priceSymbol='NEW', filedDate='2025-01-07'),
                       row('sale', priceSymbol='S00', action='sell', tradeDate='2025-01-07', filedDate='2025-01-07')]
        p = replay(rs)
        self.assertEqual(p['rotation']['forcedExits'], 1)
        self.assertEqual(p['unmatchedSales'], 1)
        self.assert_accounting(p)
        self.assertEqual(replay(rs, exit_price='hold')['rotation']['forcedExits'], 1)

    def test_cap_or_missing_entry_price_never_forces_sale(self):
        rs = [row(f'b{i:02}') for i in range(8)]
        p = replay(rs)
        self.assertGreater(p['cashDiagnostics']['capSkipped'], 0)
        self.assertEqual(p['rotation']['forcedExits'], 0)
        self.assert_accounting(p)
        rs = seed() + [row('new', priceSymbol='MISSING', filedDate='2025-01-06')]
        hs = {s: {'bars': bars()} for s in {r['priceSymbol'] for r in rs} | {'SPY'}}
        hs['MISSING']['bars'] = {}
        self.assertEqual(replay(rs, hs)['rotation']['forcedExits'], 0)

    def test_cutoff_pending_and_future_returns_cannot_change_choices(self):
        rs = seed(21)
        p = replay(rs, end='2025-01-06')
        self.assertEqual(p['rotation']['pendingCount'], 1)
        self.assertEqual(p['executedBuys'], 20)
        self.assert_accounting(p)
        hs = {s: {'bars': bars()} for s in {r['priceSymbol'] for r in rs} | {'SPY'}}
        for h in hs.values():
            for d in DAYS:
                if d > '2025-01-06': h['bars'][d] = {**h['bars'][d], 'close': 999999.}
        self.assertEqual(p['orders'], replay(rs, hs, end='2025-01-06')['orders'])


import collections
if __name__ == '__main__': unittest.main()
