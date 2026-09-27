import copy
import json
import math
import unittest
from backend.app.research.lab_engine.reported_positions import reconstruct_positions

def row(id='buy', **changes):
    return {'id': id, 'filingId': 'filing-' + id, 'politicianId': 'p1', 'politician': 'Member One',
            'chamber': 'house', 'owner': 'Self', 'ownerRaw': 'SELF', 'account': 'IRA', 'accountId': 'ira-1',
            'securityId': 'common-stock-a', 'asset': 'Company A common stock', 'assetType': 'ST',
            'tickerReported': 'AAA', 'ticker': 'AAA', 'priceSymbol': 'AAA',
            'tradeDate': '2025-01-10', 'filedDate': '2025-02-01', 'signalStatus': 'new',
            'sourceVerified': True, 'source': 'https://example.test/official/' + id,
            'sourceSha256': 'source-sha', 'sourcePage': 1, 'sourceRow': 1,
            'transactionType': 'Purchase', 'action': 'buy', 'amountLow': 1001, 'amountHigh': 15000,
            'amountRange': '$1,001 - $15,000', 'eligible': True, **changes}

def one(rows, date='2025-03-01', **kwargs):
    result = reconstruct_positions(rows, date, **kwargs)
    assert len(result['positions']) == 1, result
    return result['positions'][0]

class EconomicPositionsTest(unittest.TestCase):
    def test_late_filing_unavailable_to_point_in_time_view(self):
        result = reconstruct_positions([row()], '2025-01-20')
        self.assertEqual(result['positions'], [])
        self.assertEqual(result['counts']['not_known_by_cutoff'], 1)
        self.assertTrue(result['coverage']['noRowsDoesNotMeanNoHoldings'])

    def test_retrospective_trade_view_explicitly_uses_later_knowledge(self):
        result = reconstruct_positions([row()], '2025-01-20', knowledge_as_of='2025-02-10')
        self.assertEqual(result['temporalMode'], 'retrospective_trade_date')
        self.assertEqual(result['positions'][0]['sourceRowIds'], ['buy'])

    def test_retrospective_view_still_excludes_trades_after_position_date(self):
        result = reconstruct_positions([row(tradeDate='2025-01-21')], '2025-01-20', knowledge_as_of='2025-02-10')
        self.assertEqual(result['positions'], [])

    def test_filing_date_proxy_and_strict_public_are_distinct(self):
        self.assertIn('public_release_time_unknown_filing_date_proxy', one([row()])['warnings'])
        self.assertEqual(reconstruct_positions([row()], '2025-03-01', availability_policy='strict_public')['positions'], [])

    def test_real_public_timestamp_takes_precedence_and_uses_calendar_zone(self):
        # 00:30 UTC on Feb 2 is still Feb 1 in New York.
        r = row(firstPublicAt='2025-02-02T00:30:00Z')
        self.assertEqual(len(reconstruct_positions([r], '2025-02-01', availability_policy='strict_public')['positions']), 1)
        self.assertEqual(reconstruct_positions([r], '2025-01-31')['positions'], [])

    def test_naive_public_timestamp_is_not_silently_given_timezone(self):
        result = reconstruct_positions([row(firstPublicAt='2025-02-01T12:00:00')], '2025-03-01')
        self.assertEqual(result['positions'], [])
        self.assertEqual(result['counts']['invalid_public_timestamp_without_timezone'], 1)

    def test_sale_only_does_not_create_short_or_zero_holding(self):
        p = one([row(transactionType='Sale', action='sell')])
        self.assertEqual(p['status'], 'sale_only_opening_holding_unknown')
        self.assertIsNone(p['shares']); self.assertIsNone(p['currentValue'])

    def test_explicit_short_sale_and_cover_cannot_change_long_holdings(self):
        rows = [row(), row('short-sale', transactionType='Sale', action='sell',
                           signalStatus='source_short_position_transaction', eligible=False),
                row('short-cover', transactionType='Purchase', action='buy',
                    signalStatus='source_short_position_transaction', eligible=False)]
        result = reconstruct_positions(rows, '2025-03-01')
        self.assertEqual(result['positions'][0]['sourceRowIds'], ['buy'])
        self.assertEqual(result['counts']['short_position_transaction_not_a_long_holding'], 2)
        self.assertEqual({r['sourceRowId'] for r in result['excludedKnownRows']}, {'short-sale', 'short-cover'})

    def test_dollar_sale_larger_than_purchase_does_not_close_position(self):
        # Appreciation or different lots can make proceeds exceed purchase cost.
        p = one([row(), row('sale', tradeDate='2025-02-10', filedDate='2025-02-11',
                           transactionType='Sale', action='sell', amountLow=15001, amountHigh=50000)])
        self.assertEqual(p['status'], 'sale_remainder_unknown')
        self.assertIsNone(p['shares']); self.assertIsNone(p['costBasis'])
        self.assertEqual(len(p['reportedAmountRanges']), 2)

    def test_partial_sale_preserves_unquantified_remainder(self):
        p = one([row(), row('partial', tradeDate='2025-02-10', filedDate='2025-02-11',
                           transactionType='Sale (Partial)', action='sell')])
        self.assertEqual(p['status'], 'partial_sale_remainder_unquantified')
        self.assertIsNone(p['shares']); self.assertIsNone(p['weight'])

    def test_full_sale_is_source_event_not_actual_zero_share_assertion(self):
        p = one([row(), row('full', tradeDate='2025-02-10', filedDate='2025-02-11',
                           transactionType='Sale (Full)', action='sell')])
        self.assertEqual(p['status'], 'full_sale_reported')
        self.assertEqual(p['actualHoldingStatus'], 'unknown'); self.assertIsNone(p['shares'])

    def test_unknown_account_cannot_match_purchase_and_full_sale(self):
        p = one([row(account=None, accountId=None), row('full', account=None, accountId=None,
                         tradeDate='2025-02-10', filedDate='2025-02-11', transactionType='Sale (Full)', action='sell')])
        self.assertEqual(p['status'], 'full_sale_scope_uncertain')
        self.assertFalse(p['identityConfirmed'])
        self.assertIn('unknown_account_evidence_bucket_may_combine_accounts', p['warnings'])

    def test_same_reported_account_label_is_not_unique_account_id(self):
        p = one([row(accountId=None, transactionType='Sale (Full)', action='sell')])
        self.assertEqual(p['status'], 'full_sale_scope_uncertain')

    def test_owner_account_politician_and_security_are_separate(self):
        rows = [row(), row('spouse', owner='Spouse'), row('other-account', accountId='ira-2'),
                row('other-member', politicianId='p2'), row('other-security', securityId='preferred-a')]
        self.assertEqual(len(reconstruct_positions(rows, '2025-03-01')['positions']), 5)
        self.assertEqual(len(reconstruct_positions(rows, '2025-03-01', politician_id='p2')['positions']), 1)

    def test_unknown_owner_is_not_inferred_as_self(self):
        p = one([row(owner='Unknown', ownerRaw=None)])
        self.assertIsNone(p['owner']); self.assertFalse(p['identityConfirmed'])

    def test_numbered_children_have_separate_position_buckets(self):
        rows = [row('child1-buy', owner='Dependent', ownerRaw='Child', dependentChildId='1'),
                row('child2-buy', owner='Dependent', ownerRaw='Child', dependentChildId='2'),
                row('child1-sale', owner='Dependent', ownerRaw='Child', dependentChildId='1',
                    transactionType='Sale (Full)', action='sell', tradeDate='2025-02-10', filedDate='2025-02-11')]
        ps = reconstruct_positions(rows, '2025-03-01')['positions']
        self.assertEqual(len(ps), 2)
        by_child = {p['dependentChildId']: p for p in ps}
        self.assertEqual(by_child['1']['status'], 'full_sale_reported')
        self.assertEqual(by_child['2']['sourceRowIds'], ['child2-buy'])
        self.assertEqual(by_child['2']['status'], 'purchase_reported_holding_unconfirmed')

    def test_unknown_child_does_not_close_numbered_child(self):
        rows = [row('numbered', owner='Dependent', ownerRaw='Child', dependentChildId='2'),
                row('unknown-sale', owner='Dependent', ownerRaw='Child',
                    transactionType='Sale (Full)', action='sell', tradeDate='2025-02-10', filedDate='2025-02-11')]
        ps = reconstruct_positions(rows, '2025-03-01')['positions']
        self.assertEqual(len(ps), 2)
        numbered = next(p for p in ps if p['dependentChildId']=='2')
        unknown = next(p for p in ps if p['dependentChildId'] is None)
        self.assertEqual(numbered['sourceRowIds'], ['numbered'])
        self.assertEqual(unknown['status'], 'full_sale_scope_uncertain')

    def test_child_number_not_inferred_from_comment_by_position_engine(self):
        p = one([row(owner='Dependent', ownerRaw='Child', comment='Child #2')], include_evidence=True)
        self.assertIsNone(p['dependentChildId'])
        self.assertEqual(p['evidence'][0]['sourceComment'], 'Child #2')

    def test_explicitly_unverified_child_identifier_is_not_used(self):
        p = one([row(owner='Dependent', ownerRaw='Child', dependentChildId='2', dependentChildIdVerified=False)])
        self.assertIsNone(p['dependentChildId'])

    def test_child_source_comment_and_number_evidence_are_preserved(self):
        evidence = {'source_field': 'Comment', 'raw': 'Underlying asset of dependent child #3'}
        p = one([row(owner='Dependent', ownerRaw='Child', dependentChildId='3', dependentChildIdVerified=True,
                     dependentChildIdEvidence=evidence, comment=evidence['raw'])], include_evidence=True)
        self.assertEqual(p['dependentChildId'], '3')
        self.assertEqual(p['evidence'][0]['dependentChildIdEvidence'], evidence)

    def test_price_symbol_does_not_merge_different_securities(self):
        rows = [row(securityId=None), row('different', securityId=None, asset='Company A preferred series B', tickerReported='AAA-B', priceSymbol='AAA')]
        self.assertEqual(len(reconstruct_positions(rows, '2025-03-01')['positions']), 2)

    def test_derived_ticker_does_not_create_historical_source_identity(self):
        p = one([row(securityId=None, tickerReported=None, ticker='OLD'),
                 row('later-mapping', securityId=None, tickerReported=None, ticker='NEW')])
        self.assertIsNone(p['ticker'])
        self.assertEqual(p['tickerStatus'], 'not_reported')
        self.assertEqual(p['sourceRowCount'], 2)

    def test_verified_canonical_id_can_bridge_reported_name_change(self):
        p = one([row(), row('rename', asset='Renamed Company A', tickerReported='NEW')])
        self.assertEqual(p['sourceRowCount'], 2)

    def test_option_contracts_not_merged_as_underlying_stock(self):
        rows = [row('call1', securityId=None, asset='Company A call option'), row('call2', securityId=None, asset='Company A call option')]
        result = reconstruct_positions(rows, '2025-03-01')
        self.assertEqual(len(result['positions']), 2)
        self.assertTrue(all(p['securityStatus']=='unresolved_instrument_kept_per_source_row' for p in result['positions']))

    def test_exchange_is_preserved_despite_follower_ineligibility(self):
        p = one([row(transactionType='Exchange', action='other', eligible=False, exclusion='Unsupported transaction type')])
        self.assertEqual(p['status'], 'exchange_result_unknown')

    def test_missing_price_does_not_remove_reported_position(self):
        p = one([row(eligible=False, priceSymbol='', exclusion='Missing or unresolved ticker')])
        self.assertEqual(p['sourceRowIds'], ['buy'])

    def test_reinvestment_is_real_source_evidence_not_new_follower_order(self):
        p = one([row(economicEvent='dividend_reinvestment', eligible=False)])
        self.assertEqual(p['transactionCounts'], {'dividend_reinvestment': 1})

    def test_repeated_filing_does_not_add_position_event(self):
        result = reconstruct_positions([row(), row('repeat', signalStatus='repeated_trade_content', repeatedTradeContentOf={'filingId':'filing-buy'})], '2025-03-01')
        self.assertEqual(result['positions'][0]['sourceRowCount'], 1)
        self.assertEqual(result['counts']['repeated_source_content'], 1)

    def test_amendment_effect_does_not_leak_before_its_filing(self):
        amended = row('correction', signalStatus='correction_or_unknown', originalTransactionId='buy',
                      filedDate='2025-04-01', transactionType='Sale (Full)', action='sell')
        before = one([row(), amended], '2025-03-01')
        after = one([row(), amended], '2025-04-01')
        self.assertEqual(before['status'], 'purchase_reported_holding_unconfirmed')
        self.assertEqual(after['status'], 'correction_pending')
        self.assertEqual(after['sourceRowIds'], ['buy'])
        self.assertEqual(after['linkedCorrectionSourceRowIds'], ['correction'])

    def test_newly_disclosed_amendment_row_is_allowed_once(self):
        self.assertEqual(one([row(signalStatus='newly_disclosed_amendment')])['sourceRowCount'], 1)

    def test_future_versioned_signal_override_does_not_rewrite_past(self):
        r = row(signalStatus='correction_or_unknown', signalStatusEffectiveDate='2025-04-01', originalSignalStatus='new')
        self.assertEqual(one([r])['sourceRowCount'], 1)
        self.assertEqual(reconstruct_positions([r], '2025-04-01')['positions'], [])

    def test_same_day_buy_sale_cannot_depend_on_source_row_order(self):
        rows = [row(), row('full', transactionType='Sale (Full)', action='sell')]
        self.assertEqual(one(rows)['status'], 'same_day_order_unknown')
        self.assertEqual(one(rows), one(list(reversed(rows))))

    def test_trade_chronology_not_late_filing_order_controls_latest_event(self):
        late_buy = row(filedDate='2025-03-01')
        sale = row('sale', tradeDate='2025-02-10', filedDate='2025-02-11', transactionType='Sale', action='sell')
        p = one([sale, late_buy])
        self.assertEqual(p['latestTradeDate'], '2025-02-10')
        self.assertEqual(p['latestKnownDate'], '2025-03-01')
        self.assertEqual(p['status'], 'sale_remainder_unknown')

    def test_source_extraction_problems_are_visible_and_not_economic_events(self):
        result = reconstruct_positions([row(exclusion='Source document extraction needs review')], '2025-03-01')
        self.assertEqual(result['positions'], [])
        self.assertEqual(result['excludedKnownRows'][0]['reason'], 'source_extraction_needs_review')

    def test_invalid_date_is_quarantined(self):
        result = reconstruct_positions([row(tradeDate='2025-02-30')], '2025-03-01')
        self.assertEqual(result['positions'], [])
        self.assertEqual(result['excludedKnownRows'][0]['reason'], 'trade_date_missing_or_invalid')

    def test_identical_ids_deduplicate_but_identical_separate_rows_survive(self):
        r = row(); p = one([r, copy.deepcopy(r), row('distinct-source-row')])
        self.assertEqual(p['sourceRowCount'], 2)

    def test_conflicting_same_source_id_fails_closed(self):
        with self.assertRaises(ValueError):
            reconstruct_positions([row(), row(transactionType='Sale', action='sell')], '2025-03-01')

    def test_nan_amounts_do_not_leak_into_json_or_valuations(self):
        result = reconstruct_positions([row(amountLow=math.nan, amountHigh=math.inf)], '2025-03-01')
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['positions'][0]['reportedAmountRanges'][0]['status'], 'unresolved')

    def test_input_immutable_and_evidence_source_ranges_retained(self):
        rows = [row()]; original = copy.deepcopy(rows)
        p = one(rows, include_evidence=True)
        self.assertEqual(rows, original)
        self.assertEqual(p['evidence'][0]['amountRange']['low'], 1001)
        self.assertEqual(p['evidence'][0]['sourceSha256'], 'source-sha')

if __name__ == '__main__':
    unittest.main(verbosity=2)
