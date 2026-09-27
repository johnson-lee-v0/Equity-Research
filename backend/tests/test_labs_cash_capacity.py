"""Cash attribution, conservation and spendability tests; synthetic data only."""
import unittest
from backend.app.research.lab_engine.cash_capacity import request_capacity, summarize_cash
from backend.tests.lab_fixtures import row, bars, run, DAYS
from backend.app.research.lab_engine.backtest import Engine

class CashCapacityTests(unittest.TestCase):
    def test_cap_cash_minimum_decomposition(self):
        for args in ((2000,1000,500,500.5,.5),(2000,0,0,0,0),(2000,2000,.5,.5005,.00025),(.5,.5,.5,1000,1),(0,0,0,1000,0)):
            d=request_capacity(*args)
            self.assertAlmostEqual(d['targetNotional'],sum(d[k] for k in ('capShortfallNotional','cashShortfallNotional','executedNotional','minimumFillRemainder')))
        self.assertEqual(request_capacity(2000,1000,500,500.5,.5)['reason'],'cash_and_cap_scaled')
        self.assertEqual(request_capacity(2000,0,0,0,0)['reason'],'ticker_cap')
        self.assertEqual(request_capacity(2000,2000,.5,.5005,.00025)['reason'],'cash')
        self.assertEqual(request_capacity(0,0,0,1000,0)['reason'],'sizing_zero')
    def test_positive_closing_cash_does_not_rewrite_same_day_rejection(self):
        rows=[row('b'+str(i),priceSymbol='S'+str(i),ticker='S'+str(i)) for i in range(20)]
        rows += [row('sale',priceSymbol='S0',ticker='S0',action='sell',tradeDate='2025-01-07',filedDate='2025-01-08'),
                 row('same-day',priceSymbol='BBB',ticker='BBB',tradeDate='2025-01-08',filedDate='2025-01-08'),
                 row('next-day',priceSymbol='BBB',ticker='BBB',tradeDate='2025-01-09',filedDate='2025-01-09')]
        histories={s:{'bars':bars()} for s in {r['priceSymbol'] for r in rows}|{'SPY'}}
        p=run(rows,histories=histories,sizing='fixed_5');orders={o['id']:o for o in p['orders']}
        self.assertEqual(orders['same-day']['capacity']['reason'],'cash')
        self.assertLess(orders['same-day']['capacity']['spendableCash'],1)
        self.assertGreater(next(d for d in p['curve'] if d['date']==orders['same-day']['date'])['cash'],1)
        self.assertGreater(orders['next-day']['notional'],0)
        self.assertEqual(sum(o['id']=='same-day' for o in p['orders']),1)
        d=p['cashDiagnostics'];self.assertEqual(d['cashSkipped'],1);self.assertEqual(d['capSkipped'],0)
        self.assertLess(abs(d['cashReconciliationError']),.001)
        self.assertEqual(sum(y['cashSkipped'] for y in d['years']),d['cashSkipped'])
        self.assertEqual(sum(y['executedBuys'] for y in d['years']),d['executedBuys'])
    def test_unknown_old_rejections_are_not_called_cash(self):
        p={'curve':[{'date':'2025-01-02','cash':100000,'nav':100000}],
           'orders':[{'id':'x','date':'2025-01-02','action':'buy','notional':0,'status':'No cash, no Kelly edge or ticker cap'}]}
        d=summarize_cash(p);self.assertFalse(d['available']);self.assertEqual(d['cashSkipped'],0);self.assertEqual(d['unknownSkipReasons'],1)
    def test_fee_reconciliation_catches_cash_creation(self):
        p={'curve':[{'date':'2025-01-02','cash':100001,'nav':100001}],'orders':[]}
        with self.assertRaises(ValueError):summarize_cash(p)
    def test_legacy_crypto_weekend_start_and_weekend_only(self):
        days=['2020-01-03','2020-01-06'];prices={'SPY':{d:{'open':100.,'close':102.} for d in days},
                 'BTC-USD':{d:{'open':100.,'close':100.} for d in ['2020-01-03','2020-01-04','2020-01-05','2020-01-06']}}
        rows=[row('crypto',assetClass='crypto',priceSymbol='BTC-USD',ticker='BTC-USD',tradeDate='2020-01-02',filedDate='2020-01-03')]
        e=Engine(rows,prices);r=e.simulate(rows,start='2020-01-04',end='2020-01-06')
        self.assertEqual([p['benchmark'] for p in r['curve']],[100000.,100000.,102000.])
        weekend=e.simulate(rows,start='2020-01-04',end='2020-01-05')
        self.assertTrue(all(p['benchmark']==100000. for p in weekend['curve']))
        self.assertGreater(weekend['executedBuys'],0)
        self.assertLess(abs(weekend['cashDiagnostics']['cashReconciliationError']),.001)

if __name__=='__main__':unittest.main()
