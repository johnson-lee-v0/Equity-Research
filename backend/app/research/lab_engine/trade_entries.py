"""One display row per executed purchase, reconciled to the plotted cash account."""
from .backtest import owner_key


def entry_ledger(result, byid, prices):
    live = {}
    finished = []

    def key(r):
        return (r['politicianId'], owner_key(r), r.get('account') or 'Unknown account', r['priceSymbol'])

    for order in result['orders']:
        if order.get('notional', 0) <= 0:
            continue
        # Rotation sales are portfolio decisions, not politician disclosures.
        r = None if order.get('synthetic') else byid[order['id']]
        symbol = order['symbol'] if r is None else r['priceSymbol']
        bar = prices[symbol][order['date']]
        unit_price = order.get('executionPrice', bar['open'])
        shown = order.get('quotePrice', unit_price)
        if order['action'] == 'buy':
            live[r['id']] = {
                'id': r['id'], 'key': key(r), 'politicianId': r['politicianId'], 'politician': r['politician'],
                'symbol': r['priceSymbol'], 'ticker': r['ticker'], 'asset': r['asset'], 'owner': r['owner'],
                'dependentChildId': r.get('dependentChildId'), 'account': r.get('account'),
                'entryDate': order['date'], 'entryPrice': shown, 'entryUnitPrice': unit_price,
                'entryPriceBasis': 'vendor_split_quote' if order.get('quotePrice') is not None else 'total_return_adjusted',
                'cost': order['notional'] + order.get('fee', 0), 'entryFee': order.get('fee', 0),
                'units': order['notional'] / unit_price, 'sourceIds': [r['id']],
                'tradeDate': r['tradeDate'], 'filedDate': r['filedDate'], 'quantityType': 'synthetic_total_return_units',
            }
        else:
            ids = order.get('purchaseSourceIds')
            closing = [p for p in live.values() if p['id'] in ids] if ids is not None else [p for p in live.values() if p['key'] == key(r)]
            for p in closing:
                gross = p['units'] * unit_price
                fee = gross * (order['fee'] / order['notional'])
                proceeds = gross - fee
                p.update(status='closed', exitDate=order['date'], markDate=order['date'], currentOrExitPrice=shown,
                         currentOrExitUnitPrice=unit_price, value=proceeds, exitFee=fee,
                         pnl=proceeds-p['cost'], returnValue=proceeds/p['cost']-1,
                         sourceIds=p['sourceIds']+([r['id']] if r else []), stale=False,
                         exitReason=order.get('exitReason', 'disclosed_sale'),
                         exitOrderId=order['id'], exitTriggerSourceId=order.get('triggerSourceId'))
                finished.append(p)
                del live[p['id']]
    end = result['curve'][-1]['date']
    holdings = {key({'priceSymbol': h['symbol'], **h}): h for h in result['holdings']}
    for p in live.values():
        h = holdings[p['key']]
        day = h['lastPriceDate']
        bar = prices[p['symbol']][day]
        unit_price = bar['close']
        shown = bar.get('quote', {}).get('close', unit_price)
        value = p['units'] * unit_price
        p.update(status='open', exitDate=None, markDate=day, currentOrExitPrice=shown,
                 currentOrExitUnitPrice=unit_price, value=value, exitFee=0,
                 pnl=value-p['cost'], returnValue=value/p['cost']-1, stale=h['stale'], asOf=end)
        finished.append(p)
    for p in finished:
        del p['key']
    return sorted(finished, key=lambda p: (p['entryDate'], p['id']), reverse=True)
