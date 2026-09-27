"""Pure guard proposals; no I/O, orders, price substitutions or settlements."""
import datetime as dt,math,re,functools


@functools.lru_cache(maxsize=16384)
def _valid_date_text(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return False
    try:dt.date.fromisoformat(value);return True
    except ValueError:return False

def _valid_date(value):
    return isinstance(value,str) and _valid_date_text(value)

def invalid_adjusted_close_indices(response):
    """A positive quoted security cannot have nonpositive total-return units.

    Rejecting only the bad dates would bridge across a broken adjustment chain.
    Missing observations remain missing; this guard never repairs source prices.
    """
    quotes=response['indicators']['quote'][0]['close']
    adjusted=response['indicators']['adjclose'][0]['adjclose']
    times=response['timestamp']
    if len(quotes)!=len(times) or len(adjusted)!=len(times):
        raise ValueError('original_adjustment_array_length_mismatch')
    invalid=[]
    for i,(quote,value) in enumerate(zip(quotes,adjusted)):
        valid_quote=isinstance(quote,(int,float)) and not isinstance(quote,bool) and math.isfinite(quote) and quote>0
        if valid_quote and value is not None and (not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<=0):
            invalid.append(i)
    return invalid

def audit_price_history(cache,policy,*,expected_sessions=None,cache_sha256=None):
    """Filter bars using source-verified identity boundaries; report completeness.

    expected_sessions is supplied by the existing market calendar and restricted
    by the caller to the desired listed-security historical period. It must not
    include pre-IPO sessions. Missing bars stay missing. Original cache is intact.
    Review flags are data-quality findings, not automatic source-row vetoes or
    permission to backfill prices. Every order still needs its exact session.
    """
    if cache.get('symbol')!=policy['symbol']:raise ValueError('Price policy symbol mismatch')
    boundary=policy.get('lastTradableSession')
    if boundary is not None and not _valid_date(boundary):raise ValueError('Invalid reviewed last session')
    issues=[]
    if cache_sha256!=policy.get('expectedCacheSha256'):issues.append('cache_changed_since_source_audit')
    currency=(cache.get('meta') or {}).get('currency')
    if currency!='USD':issues.append('non_usd_or_unknown_currency_requires_explicit_currency_policy')
    kept=[];discarded=[];invalid=[];seen=set();duplicates=set()
    for bar in cache.get('bars',[]):
        day=bar.get('date')
        if not _valid_date(day):invalid.append(day);continue
        if boundary and day>boundary:discarded.append(day);continue
        if day in seen:duplicates.add(day)
        seen.add(day)
        if any(not isinstance(bar.get(k),(int,float)) or isinstance(bar[k],bool) or not math.isfinite(bar[k]) or bar[k]<=0 for k in ('open','close')):
            invalid.append(day);continue
        kept.append({'date':day,'open':bar['open'],'close':bar['close']})
    kept=sorted((b for b in kept if b['date'] not in duplicates),key=lambda b:b['date'])
    if currency!='USD':kept=[]
    quarantined=policy.get('historyQuarantined',False)
    if not isinstance(quarantined,bool):raise ValueError('Invalid price quarantine policy')
    if quarantined:
        if not isinstance(policy.get('quarantineReason'),str) or not policy['quarantineReason'].strip():raise ValueError('Price quarantine requires an evidence-backed reason')
        kept=[];issues.append('price_history_quarantined')
    if discarded:issues.append('post_listing_vendor_bars_ignored')
    if invalid:issues.append('invalid_vendor_bars_ignored')
    if duplicates:issues.append('duplicate_dates_require_review')
    available={b['date'] for b in kept}
    missing=None
    if expected_sessions is None:issues.append('historical_session_coverage_not_checked')
    else:
        expected={d for d in expected_sessions if _valid_date(d) and (not boundary or d<=boundary)}
        missing=sorted(expected-available)
        if missing:issues.append('historical_sessions_missing')
    return dict(symbol=cache['symbol'],bars=kept,originalBars=len(cache.get('bars',[])),retainedBars=len(kept),discardedAfterLastSession=sorted(discarded),invalidDates=invalid,duplicateDates=sorted(duplicates),missingExpectedDates=missing,historyComplete=missing==[] and not quarantined and not any(x in issues for x in ['cache_changed_since_source_audit','non_usd_or_unknown_currency_requires_explicit_currency_policy','invalid_vendor_bars_ignored','duplicate_dates_require_review']),issues=issues,lastTradableSession=boundary,settlementStatus=policy.get('settlementStatus'),corporateActionCashApplied=False,acquirerSubstitutionApplied=False,historyQuarantined=quarantined,quarantineReason=policy.get('quarantineReason') if quarantined else None)

