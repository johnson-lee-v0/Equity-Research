"""Original synthetic Congress replay fixtures, no archive or prototype imports."""
from backend.app.research.lab_engine.senate_backtest import SenateEngine
from backend.app.research.lab_engine.trade_entries import entry_ledger

DAYS=['2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08','2025-01-09','2025-01-10','2025-01-13','2025-01-14']


def bars(high=102.,low=98.,close=100.):
    return {d:{'date':d,'open':100.,'high':high,'low':low,'close':close,
               'quote':{'open':100.,'high':high,'low':low,'close':close}} for d in DAYS}


def row(id,**changes):
    return {'id':id,'politicianId':'senate_p','politician':'Person','chamber':'senate','owner':'Self',
            'dependentChildId':None,'account':None,'priceSymbol':'AAA','ticker':'AAA','asset':'AAA stock',
            'assetClass':'equity','eligible':True,'exclusion':'','action':'buy','transactionType':'Purchase',
            'tradeDate':'2025-01-02','filedDate':'2025-01-03',**changes}


def run(rows, histories=None, **settings):
    histories=histories or {'AAA':{'bars':bars()},'SPY':{'bars':bars(101,99)}}
    engine=SenateEngine(rows,histories)
    result=engine.simulate_senate(rows,start=DAYS[0],end=DAYS[-1],**settings)
    result['entries']=entry_ledger(result,{r['id']:r for r in rows},engine.prices)
    return result

