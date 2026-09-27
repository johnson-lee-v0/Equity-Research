"""Conservative, bitemporal PTR position evidence. Standard-library only.

This is separate from follower simulation: source transaction-dollar ranges do
not establish actual shares, current value, portfolio weight, or opening balance.
Inputs are never mutated. No network, price access, filesystem writes or globals.
"""
from __future__ import annotations
import collections
import datetime as dt
import json
import math
import re
from zoneinfo import ZoneInfo

NEW_STATUSES = {'new', 'paper_ptr_row', 'newly_disclosed_amendment', 'original_ptr_row'}
UNKNOWN = {'', 'unknown', 'unknown account', 'unknown owner', 'not identified', 'none', 'null'}
OWNER = {'self': 'Self', 'sp': 'Spouse', 'spouse': 'Spouse', 'jt': 'Joint',
         'joint': 'Joint', 'dc': 'Dependent', 'dependent': 'Dependent', 'dependent child': 'Dependent', 'child': 'Dependent'}

def _date(value):
    try:
        return dt.date.fromisoformat(value) if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None
    except ValueError:
        return None

def _clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()

def _token(value):
    return _clean(value).casefold()

def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

def _availability(row, policy, zone):
    stamp = row.get('firstPublicAt')
    if stamp:
        try:
            instant = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if instant.tzinfo is None:
                return None, 'invalid_public_timestamp_without_timezone'
            return instant.astimezone(zone).date(), 'verified_public_timestamp'
        except (TypeError, ValueError):
            return None, 'invalid_public_timestamp'
    if policy == 'strict_public':
        return None, 'public_availability_unknown'
    # A date-only filing is included at end of its source calendar date. This
    # proxy is explicitly weaker than proof of when the public could see it.
    return _date(row.get('filedDate')), 'filing_date_proxy'

def _identity(row):
    owner_raw = _clean(row.get('ownerRaw'))
    owner = OWNER.get(_token(row.get('owner'))) or OWNER.get(_token(owner_raw))
    # This field is populated only by a source-verified parser. Never infer it
    # from names, comments, row order, or another dependent's trades here.
    child_raw = _clean(row.get('dependentChildId'))
    child_id = child_raw if owner == 'Dependent' and re.fullmatch(r'[1-9]\d*', child_raw) and row.get('dependentChildIdVerified') is not False else None
    child_status = ('explicit_source_identifier' if child_id else 'unknown_child' if owner == 'Dependent' else 'not_dependent_child')
    account = _clean(row.get('account'))
    if _token(account) in UNKNOWN:
        account = None
    account_id = _clean(row.get('accountId')) or None
    security_id = _clean(row.get('securityId')) or None
    asset = _clean(row.get('asset'))
    # The normalizer may populate `ticker` from a later mapping when no symbol
    # appeared in the source. Keep source absence explicit here.
    ticker = _clean(row.get('tickerReported')) or None
    asset_type = _clean(row.get('assetType')) or None
    # Today's priceSymbol can reflect a later ticker change or an underlying
    # stock proxy. It must never silently determine historical security identity.
    if security_id:
        security_key = ('canonical', security_id)
        security_status = 'upstream_canonical_security_id'
    else:
        security_key = ('reported_description', _token(asset_type), _token(ticker), _token(asset))
        security_status = 'exact_reported_description_bucket'
        # Option terms and bond identifiers can be absent from the asset name.
        # Without a canonical ID, avoid combining contracts or bond issues.
        complex_instrument = bool(re.search(r'\boption\b|\boptions\b|\bcall\b|\bput\b|\bbond\b|\bnote\b|\bbill\b', asset + ' ' + str(asset_type), re.I)) or row.get('assetClass') == 'bond'
        if complex_instrument or not asset:
            security_key += ('source_row', str(row.get('id')))
            security_status = 'unresolved_instrument_kept_per_source_row'
    owner_key = (owner, child_id or 'UNKNOWN_CHILD') if owner == 'Dependent' else (owner or 'UNRESOLVED_OWNER',)
    key = (str(row.get('politicianId')), owner_key,
           ('account_id', account_id) if account_id else ('reported_account_label', _token(account)) if account else ('UNRESOLVED_ACCOUNT',), security_key)
    return key, {'owner': owner, 'ownerRaw': owner_raw or None,
                 'ownerStatus': 'reported_owner' if owner else 'unknown',
                 'dependentChildId': child_id, 'dependentChildIdStatus': child_status,
                 'account': account, 'accountId': account_id,
                 'accountStatus': 'upstream_account_id' if account_id else 'reported_label_not_unique_account_id' if account else 'unknown',
                 'securityId': security_id, 'securityStatus': security_status,
                 'asset': asset or None, 'ticker': ticker, 'tickerStatus': 'reported' if ticker else 'not_reported', 'assetType': asset_type,
                 'identityConfirmed': bool(owner and account_id and security_id and (owner != 'Dependent' or child_id))}

def _event_type(row):
    transaction = _token(row.get('transactionType'))
    if 'exchange' in transaction:
        return 'exchange'
    if 'sale' in transaction or row.get('action') == 'sell':
        if 'partial' in transaction or row.get('partialSale') is True:
            return 'partial_sale'
        if 'full' in transaction:
            return 'full_sale'
        return 'sale_unspecified'
    if transaction in {'purchase', 'buy'} or row.get('action') == 'buy':
        return 'dividend_reinvestment' if row.get('economicEvent') == 'dividend_reinvestment' else 'purchase'
    return 'unknown_event'

def _amount(row):
    low, high = _number(row.get('amountLow')), _number(row.get('amountHigh'))
    valid = low is not None and (high is None or high >= low)
    return {'low': low if valid else None, 'high': high if valid else None,
            'label': row.get('amountRange'), 'currency': row.get('currency'),
            'basis': 'reported_transaction_amount_not_position_value',
            'status': 'range_or_open_upper_bound' if valid else 'unresolved',
            'rawLow': row.get('amountLow') if isinstance(row.get('amountLow'), str) else _number(row.get('amountLow')),
            'rawHigh': row.get('amountHigh') if isinstance(row.get('amountHigh'), str) else _number(row.get('amountHigh'))}

def _evidence(row, available, basis):
    return {'sourceRowId': row['id'], 'filingId': row.get('filingId'),
            'tradeDate': row.get('tradeDate'), 'filedDate': row.get('filedDate'),
            'knownDate': available.isoformat(), 'availabilityBasis': basis,
            'eventType': _event_type(row), 'transactionType': row.get('transactionType'),
            'amountRange': _amount(row), 'ownerRaw': row.get('ownerRaw'),
            'dependentChildId': row.get('dependentChildId'),
            'dependentChildIdEvidence': row.get('dependentChildIdEvidence'),
            'sourceComment': row.get('comment'),
            'tradeDateRaw': row.get('tradeDateRaw'), 'notificationDate': row.get('notificationDate'),
            'notificationDateRaw': row.get('notificationDateRaw'),
            'ownerMarkerScope': row.get('ownerMarkerScope'), 'ownerSourceEvidence': row.get('ownerSourceEvidence'),
            'sourcePositionEffect': row.get('sourcePositionEffect'),
            'account': row.get('account'), 'asset': row.get('asset'),
            'source': row.get('source'), 'sourceSha256': row.get('sourceSha256'),
            'sourcePage': row.get('sourcePage'), 'sourceRow': row.get('sourceRow')}

def _source_rejection(row):
    if row.get('sourceVerified') is not True:
        return 'source_not_verified'
    if row.get('signalStatus') == 'source_short_position_transaction':
        return 'short_position_transaction_not_a_long_holding'
    if row.get('extractionNeedsReview') or 'source document extraction needs review' in _token(row.get('exclusion')):
        return 'source_extraction_needs_review'
    if any('unverified_source_ocr_candidate' in str(flag) for flag in row.get('qaFlags', [])):
        return 'source_extraction_needs_review'
    return None

def _status(events, identity, corrections):
    if corrections:
        return 'correction_pending', 'A disclosed correction may affect this position; its economic effect is unresolved.'
    latest_day = max(e['tradeDate'] for e in events)
    latest = {e['eventType'] for e in events if e['tradeDate'] == latest_day}
    if len(latest) > 1:
        return 'same_day_order_unknown', 'Different actions share the latest trade date; intraday order is not disclosed.'
    action = next(iter(latest))
    all_types = {e['eventType'] for e in events}
    if action in {'purchase', 'dividend_reinvestment'}:
        return 'purchase_reported_holding_unconfirmed', 'An acquisition was reported; remaining shares and current holding are unconfirmed.'
    if action == 'partial_sale':
        return 'partial_sale_remainder_unquantified', 'A partial sale indicates a remainder at that transaction; its size and present holding are unknown.'
    if action == 'full_sale':
        if identity['identityConfirmed']:
            return 'full_sale_reported', 'A full sale was reported for this identified bucket; no present-day quantity is inferred.'
        return 'full_sale_scope_uncertain', 'A full sale was reported; unresolved owner, account, or security scope prevents closing other reported purchases.'
    if action == 'sale_unspecified':
        if not all_types.intersection({'purchase', 'dividend_reinvestment'}):
            return 'sale_only_opening_holding_unknown', 'A sale implies an earlier holding, but its opening and remaining size are unknown.'
        return 'sale_remainder_unknown', 'Sale proceeds do not establish shares sold or whether the earlier holding was closed.'
    if action == 'exchange':
        return 'exchange_result_unknown', 'An exchange was reported; acquired/disposed quantities and replacement security are unresolved.'
    return 'reported_activity_unresolved', 'The source reports activity whose position effect is unresolved.'

def reconstruct_positions(rows, as_of, politician_id=None, knowledge_as_of=None,
                          availability_policy='filing_date_proxy', include_evidence=False,
                          calendar_timezone='America/New_York', coverage=None):
    """Return source-evidence positions through as_of, using knowledge_as_of only.

    Dates are ISO YYYY-MM-DD, interpreted through end of that calendar date.
    knowledge_as_of defaults to as_of. A later value is explicitly retrospective.
    strict_public requires a timezone-aware firstPublicAt; the default uses the
    filing date when that timestamp is unavailable and labels every such use.

    `eligible` belongs to follower simulation and is deliberately not a filter.
    Caller-supplied securityId/accountId must be stable historical identifiers.
    Unknown account/owner groups are evidence rollups, not matched trade lots.
    """
    target, knowledge = _date(as_of), _date(knowledge_as_of or as_of)
    if target is None or knowledge is None:
        raise ValueError('as_of and knowledge_as_of must be real ISO calendar dates')
    if availability_policy not in {'filing_date_proxy', 'strict_public'}:
        raise ValueError('Unknown availability_policy')
    zone = ZoneInfo(calendar_timezone)
    groups, rejected, corrections = {}, [], []
    counters = collections.Counter()
    seen = {}
    for row in rows:
        if politician_id is not None and row.get('politicianId') != politician_id:
            continue
        available, basis = _availability(row, availability_policy, zone)
        if available is None:
            counters[basis if basis != 'filing_date_proxy' else 'filing_date_missing_or_invalid'] += 1
            continue
        if available > knowledge:
            counters['not_known_by_cutoff'] += 1
            continue
        identifier = row.get('id')
        if not isinstance(identifier, str) or not identifier or not row.get('politicianId'):
            counters['missing_source_or_politician_id'] += 1
            continue
        fingerprint = repr(tuple(row.get(k) for k in ['tradeDate', 'filedDate', 'asset', 'owner', 'dependentChildId', 'account', 'securityId', 'transactionType', 'amountLow', 'amountHigh', 'signalStatus', 'sourceSha256']))
        if identifier in seen:
            if seen[identifier] != fingerprint:
                raise ValueError('Conflicting normalized records share source row ID: ' + identifier)
            counters['duplicate_source_row_id'] += 1
            continue
        seen[identifier] = fingerprint
        evidence = _evidence(row, available, basis)
        traded, filed = _date(row.get('tradeDate')), _date(row.get('filedDate'))
        rejection = _source_rejection(row)
        if traded is None:
            rejection = rejection or 'trade_date_missing_or_invalid'
        elif filed and traded > filed:
            rejection = rejection or 'trade_date_after_filing'
        elif traded > target:
            counters['trade_after_position_date'] += 1
            continue
        signal = _token(row.get('signalStatus'))
        effective = _date(row.get('signalStatusEffectiveDate'))
        if effective and effective > knowledge:
            # A versioned override must not leak a later correction into history.
            signal = _token(row.get('originalSignalStatus'))
            if not signal:
                rejection = rejection or 'future_signal_override_requires_historical_version'
        if (row.get('repeatedTradeContentOf') and not (effective and effective > knowledge)) or signal == 'repeated_trade_content':
            rejection = rejection or 'repeated_source_content'
        elif signal not in NEW_STATUSES:
            rejection = rejection or 'amended_deleted_or_unresolved_signal'
        if rejection:
            rejected.append({'sourceRowId': identifier, 'politicianId': row.get('politicianId'),
                             'reason': rejection, 'tradeDate': row.get('tradeDate'),
                             'filedDate': row.get('filedDate'), 'source': row.get('source'),
                             'sourcePage': row.get('sourcePage'),
                             'originalTransactionId': row.get('originalTransactionId'),
                             **({'evidence': evidence} if include_evidence else {})})
            counters[rejection] += 1
            if rejection == 'amended_deleted_or_unresolved_signal':
                corrections.append(rejected[-1])
            continue
        key, identity = _identity(row)
        group = groups.setdefault(key, {'identity': identity, 'politicianId': row['politicianId'],
                                      'politician': row.get('politician'), 'chamber': row.get('chamber'),
                                      'events': []})
        group['events'].append(evidence)
        counters['included_source_rows'] += 1
        counters[basis] += 1
    positions = []
    unlinked_corrections = collections.Counter(c['politicianId'] for c in corrections if not c['originalTransactionId'])
    correction_index = collections.defaultdict(list)
    for correction in corrections:
        if correction['originalTransactionId']:
            correction_index[correction['originalTransactionId']].append(correction['sourceRowId'])
    for key, group in groups.items():
        events = sorted(group['events'], key=lambda e: (e['tradeDate'], e['knownDate'], e['sourceRowId']))
        identity = group['identity']; ids = [e['sourceRowId'] for e in events]
        linked = [correction for identifier in ids for correction in correction_index[identifier]]
        status, label = _status(events, identity, linked)
        warnings = ['PTRs_do_not_establish_opening_holdings', 'current_holdings_and_quantities_unconfirmed', 'no_share_or_dollar_netting']
        if not identity['owner']:
            warnings.append('unknown_owner_evidence_bucket')
        if identity['owner'] == 'Dependent' and not identity['dependentChildId']:
            warnings.append('unknown_child_evidence_bucket_not_matched_to_numbered_child')
        if not identity['account'] and not identity['accountId']:
            warnings.append('unknown_account_evidence_bucket_may_combine_accounts')
        elif not identity['accountId']:
            warnings.append('same_account_label_is_not_proof_of_unique_account_identity')
        if not identity['securityId']:
            warnings.append('reported_security_bucket_not_verified_canonical_identity')
        if any(e['availabilityBasis'] == 'filing_date_proxy' for e in events):
            warnings.append('public_release_time_unknown_filing_date_proxy')
        if unlinked_corrections[group['politicianId']]:
            warnings.append('member_has_unlinked_corrections')
        ranges = collections.OrderedDict()
        for event in events:
            amount = event['amountRange']; range_key = json.dumps(amount, sort_keys=True)
            entry = ranges.setdefault(range_key, {**amount, 'reportedTransactionCount': 0})
            entry['reportedTransactionCount'] += 1
        last = events[-1]
        result = {'id': json.dumps(key, separators=(',', ':')), 'politicianId': group['politicianId'],
                  'politician': group['politician'], 'chamber': group['chamber'], **identity,
                  'status': status, 'statusLabel': label, 'actualHoldingStatus': 'unknown',
                  'shares': None, 'currentValue': None, 'weight': None, 'costBasis': None,
                  'firstTradeDate': events[0]['tradeDate'], 'latestTradeDate': last['tradeDate'],
                  'latestKnownDate': max(e['knownDate'] for e in events),
                  'sourceRowIds': ids, 'sourceRowCount': len(ids),
                  'transactionCounts': dict(collections.Counter(e['eventType'] for e in events)),
                  'reportedAmountRanges': list(ranges.values()),
                  'latestSource': {k: last[k] for k in ['sourceRowId', 'source', 'sourceSha256', 'sourcePage', 'tradeDate', 'filedDate', 'amountRange']},
                  'linkedCorrectionSourceRowIds': linked, 'warnings': warnings,
                  'completeness': 'unknown', 'isSimulatedFollowerPosition': False}
        if include_evidence:
            result['evidence'] = events
        positions.append(result)
    positions.sort(key=lambda p: (str(p['politician'] or ''), str(p['ticker'] or p['asset'] or ''), p['id']))
    return {'schemaVersion': 1, 'mode': 'reported_positions', 'asOf': as_of,
            'knowledgeAsOf': knowledge.isoformat(), 'calendarTimeZone': calendar_timezone,
            'temporalMode': 'retrospective_trade_date' if knowledge > target else 'known_by_date',
            'availabilityPolicy': availability_policy, 'politicianId': politician_id,
            'positions': positions, 'positionEvidenceBucketCount': len(positions),
            'excludedKnownRows': rejected, 'counts': dict(counters),
            'coverage': {'completeHoldings': False, 'noRowsDoesNotMeanNoHoldings': True,
                         'upstream': coverage},
            'limitations': ['PTR transaction ranges do not disclose exact shares, current values, weights, or opening balances.',
                            'All positions represent reported evidence; zero holdings are never inferred from missing rows.',
                            'Unresolved owner/account/security buckets do not establish matched purchases and sales.',
                            'Historical public availability is unknown when the filing-date proxy is used.',
                            'Amended/deleted and repeated rows create no new position events; unresolved corrections remain visible.',
                            'Only explicit newly disclosed amendment rows enter as new evidence.',
                            'Current normalized source transcription is used; this is not a complete version archive of every historical filing.',
                            'No prices, synthetic follower units, assumed opening capital, or dollar-range midpoints are used.']}
