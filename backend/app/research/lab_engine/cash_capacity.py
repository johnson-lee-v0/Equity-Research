"""Cash diagnostics only. Never change fills, cash, prices or portfolio policy."""
import collections

EPS = 1e-7

def request_capacity(target, requested, allocated, budget, scale):
    """Decompose target -> ticker cap -> cash scale -> $1 minimum fill, in order."""
    target=max(0.,target);requested=max(0.,requested);allocated=max(0.,allocated)
    executed=allocated if allocated>=1 else 0.
    cap=max(0.,target-requested);cash=max(0.,requested-allocated)
    if executed:
        reason='cash_and_cap_scaled' if cash>EPS and cap>EPS else 'cash_scaled' if cash>EPS else 'ticker_cap_scaled' if cap>EPS else 'filled'
    else:
        reason='sizing_zero' if target<=EPS else 'sizing_below_minimum' if target<1 else 'ticker_cap' if requested<1 else 'cash'
    return {'targetNotional':target,'afterTickerCap':requested,'allocatedNotional':allocated,
            'executedNotional':executed,'cashShortfallNotional':cash,'capShortfallNotional':cap,
            'minimumFillRemainder':allocated-executed,'spendableCash':max(0.,budget),'cashScale':scale,
            'cashLimited':cash>EPS,'capLimited':cap>EPS,'reason':reason}


def summarize_cash(result):
    curve=result['curve'];orders=result['orders'];buys=[o for o in orders if o['action']=='buy']
    detailed=[o for o in buys if 'capacity' in o]
    counts=collections.Counter(o['capacity']['reason'] for o in detailed)
    first_empty=next((p['date'] for p in curve if p['cash']<1),None)
    first_low=next((p['date'] for p in curve if p['cash']/p['nav']<.01),None)
    cash_limited=[o for o in detailed if o['capacity']['cashLimited'] and o['capacity']['afterTickerCap']>=1]
    buy_spend=sum(o['notional']+o.get('fee',0) for o in buys if o['notional']>0)
    sale_proceeds=sum(o['notional']-o.get('fee',0) for o in orders if o['action']=='sell' and o['notional']>0)
    expected=100000+sale_proceeds-buy_spend
    if abs(expected-curve[-1]['cash'])>.001:raise ValueError('Cash ledger does not reconcile')
    initial_deployment=[o for o in buys if o['notional']>0 and first_empty and o['date']<=first_empty]
    years=[]
    for year in sorted({p['date'][:4] for p in curve}):
        ps=[p for p in curve if p['date'].startswith(year)];bs=[o for o in detailed if o['date'].startswith(year)]
        ys=[o for o in orders if o['date'].startswith(year) and o['action']=='sell' and o['notional']>0]
        years.append({'year':year,'buyOpportunities':len(bs),'executedBuys':sum(o['notional']>0 for o in bs),
            'cashSkipped':sum(o['capacity']['reason']=='cash' for o in bs),
            'cashScaledBuys':sum(o['notional']>0 and o['capacity']['cashLimited'] for o in bs),
            'capSkipped':sum(o['capacity']['reason']=='ticker_cap' for o in bs),
            'otherSkipped':sum(o['notional']==0 and o['capacity']['reason'] not in ('cash','ticker_cap') for o in bs),
            'purchaseCost':sum(o['notional']+o.get('fee',0) for o in bs),
            'netSaleProceeds':sum(o['notional']-o.get('fee',0) for o in ys),
            'cashShortfallNotional':sum(o['capacity']['cashShortfallNotional'] for o in bs),
            'averageCashWeight':sum(p['cash']/p['nav'] for p in ps)/len(ps),
            'closingCash':ps[-1]['cash']})
    periods=[];active=None
    for p in curve:
        if p['cash']<1:
            if active is None:active={'start':p['date'],'end':p['date'],'observations':0}
            active['end']=p['date'];active['observations']+=1
        elif active:periods.append(active);active=None
    if active:periods.append(active)
    longest=max(periods,key=lambda p:p['observations'],default=None)
    return {'available':len(detailed)==len(buys),'initialCash':100000,'firstBelowOneDollar':first_empty,
        'firstBelowOnePercent':first_low,'firstCashConstrainedBuy':cash_limited[0]['date'] if cash_limited else None,
        'firstCashSkippedBuy':next((o['date'] for o in detailed if o['capacity']['reason']=='cash'),None),
        'firstCashRecovery':next((p['date'] for p in curve if first_empty and p['date']>first_empty and p['cash']>=1),None),
        'firstBuyDate':next((o['date'] for o in buys if o['notional']>0),None),
        'executedBuysBeforeFirstDepletion':len(initial_deployment),
        'cashAtEnd':curve[-1]['cash'],'minimumClosingCash':min(p['cash'] for p in curve),
        'observations':len(curve),'observationsBelowOneDollar':sum(p['cash']<1 for p in curve),
        'observationsBelowOnePercent':sum(p['cash']/p['nav']<.01 for p in curve),
        'averageCashWeight':sum(p['cash']/p['nav'] for p in curve)/len(curve),
        'buyOpportunities':len(buys),'executedBuys':sum(o['notional']>0 for o in buys),
        'cashSkipped':counts['cash'],'capSkipped':counts['ticker_cap'],
        'sizingSkipped':counts['sizing_zero']+counts['sizing_below_minimum'],
        'cashScaledBuys':sum(o['notional']>0 and o['capacity']['cashLimited'] for o in detailed),
        'unknownSkipReasons':sum(o['notional']==0 and 'capacity' not in o for o in buys),
        'totalPurchaseCost':buy_spend,'totalNetSaleProceeds':sale_proceeds,
        'cashReconciliationError':expected-curve[-1]['cash'],
        'unfundedRequestedNotional':sum(o['capacity']['cashShortfallNotional'] for o in detailed),
        'longestBelowOneDollarSpell':longest,'depletionSpells':len(periods),'years':years,
        'definitions':{'fullyCommitted':'Closing cash strictly below $1; this is not a portfolio loss.',
            'cashSkipped':'Post-cap request at least $1 but cash-scaled allocation below $1; opportunity expires.',
            'cashScaled':'Positive fill smaller than its post-cap request because of the cash budget.',
            'unfundedNotional':'Sum of per-opportunity shortfalls after ticker caps, excluding fees. Not an initial-capital requirement or missed profit.',
            'calendar':'Counts refer to replay observations; eligible crypto can include weekends.',
            'noRetry':'Missed and partially filled opportunities are not queued or topped up later.'}}
