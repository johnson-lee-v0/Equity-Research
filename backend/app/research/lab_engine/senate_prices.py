"""Source-bound daily price joins. Original vendor caches are never rewritten."""
import bisect
import collections
import datetime as dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo
from .price_history import price_cache, read_price_history, read_bytes, exists

FIELDS = ('open', 'high', 'low', 'close')


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def load_ohlc(symbol):
    """Retain only raw OHLC rows that reconcile to the existing audited price cache."""
    path = price_cache() / (symbol + '.json')
    raw_path = price_cache() / (symbol + '.raw.json')
    reviewed = read_price_history(symbol)
    out = {'symbol': symbol, 'bars': {}, 'issues': list(reviewed['issues'])}
    if reviewed.get('historyQuarantined'):
        out.update(historyQuarantined=True, quarantineReason=reviewed.get('quarantineReason'))
        return out
    if 'cache_changed_since_source_audit' in reviewed['issues']:
        return out
    if not exists(path) or not exists(raw_path):
        out['issues'].append('original_ohlc_response_unavailable')
        return out
    try:
        cache = json.loads(read_bytes(path))
        raw = read_bytes(raw_path)
        sha = hashlib.sha256(raw).hexdigest()
        if sha != cache.get('rawSha256'):
            raise ValueError('original_ohlc_hash_mismatch')
        results = json.loads(raw)['chart']['result']
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError('original_ohlc_result_count_mismatch')
        response = results[0]
        if cache.get('symbol') != symbol or response['meta'].get('symbol') != symbol:
            raise ValueError('original_ohlc_symbol_mismatch')
        if response['meta'].get('currency') != 'USD':
            raise ValueError('original_ohlc_currency_not_usd')
        zone = ZoneInfo(response['meta']['exchangeTimezoneName'])
        quotes = response['indicators']['quote'][0]
        adjusted = response['indicators']['adjclose'][0]['adjclose']
        times = response['timestamp']
        if any(len(quotes.get(k, [])) != len(times) for k in FIELDS) or len(adjusted) != len(times):
            raise ValueError('original_ohlc_array_length_mismatch')
        dates = [dt.datetime.fromtimestamp(t, zone).date().isoformat() for t in times]
        counts = collections.Counter(dates)
        audited = {b['date']: b for b in reviewed['bars']}
        invalid = []
        for i, day in enumerate(dates):
            if day not in audited:
                continue
            q = {k: quotes[k][i] for k in FIELDS}
            if counts[day] != 1 or not all(positive(v) for v in [*q.values(), adjusted[i]]):
                invalid.append(day)
                continue
            tolerance = max(q.values()) * 1e-7
            if q['low'] > min(q['open'], q['close']) + tolerance or q['high'] < max(q['open'], q['close']) - tolerance or q['low'] > q['high']:
                invalid.append(day)
                continue
            factor = adjusted[i] / q['close']
            tr = {k: q[k] * factor for k in FIELDS}
            if any(not math.isclose(tr[k], audited[day][k], rel_tol=1e-9, abs_tol=1e-8) for k in ('open', 'close')):
                invalid.append(day)
                continue
            out['bars'][day] = {'date': day, **tr, 'quote': q, 'adjustmentFactor': factor}
        out.update(rawSha256=sha, cacheSha256=hashlib.sha256(read_bytes(path)).hexdigest(),
                   source=cache.get('source'), retrievedAt=cache.get('retrievedAt'), invalidOhlcDates=invalid,
                   executionBasis='Dividend-adjusted total-return units',
                   comparisonBasis='Vendor daily OHLC quote prices, on the vendor historical split basis')
        if invalid:
            out['issues'].append('invalid_or_unreconciled_ohlc_rows_excluded')
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        out['issues'].append(str(exc))
    return out


def session_for(calendar, filed, next_session=False):
    if not filed:
        return None
    i = bisect.bisect_right(calendar, filed) if next_session else bisect.bisect_left(calendar, filed)
    return calendar[i] if i < len(calendar) else None


def price_join(row, history, calendar, cutoff=None):
    """Trade date stays exact; only disclosure dates roll to a market session."""
    if not row.get('eligible'):
        history = {}  # Do not attach an underlying stock quote to an excluded option/bond/unknown identity.
    bars = history.get('bars', {})
    out = {k: row.get(k) for k in ('id', 'politicianId', 'politician', 'owner', 'account', 'asset',
                                   'priceSymbol', 'tradeDate', 'filedDate', 'action', 'source', 'sourceSha256')}
    out.update(sourceEligible=row.get('eligible', False), sourceExclusion=row.get('exclusion'),
               priceSource=history.get('source'), priceRawSha256=history.get('rawSha256'),
               comparisonBasis=history.get('comparisonBasis'), executionBasis=history.get('executionBasis'))
    dates = {'tradeDateLow': (row.get('tradeDate'), 'low'),
             'filingDateHigh': (session_for(calendar, row.get('filedDate')), 'high'),
             'filingPlusOneHigh': (session_for(calendar, row.get('filedDate'), True), 'high')}
    for key, (day, field) in dates.items():
        beyond = bool(cutoff and day and day > cutoff)
        bar = None if beyond else bars.get(day)
        out[key] = {'date': day, 'price': bar['quote'][field] if bar else None,
                    'totalReturnPrice': bar[field] if bar else None,
                    'status': 'outside_price_cutoff' if beyond else 'available' if bar else 'exact_session_price_unavailable'}
    low, high = out['tradeDateLow']['price'], out['filingPlusOneHigh']['price']
    out['nextHighBelowTradeLow'] = high < low if low is not None and high is not None else None
    return out
