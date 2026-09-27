import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from backend.app.research.lab_engine.senate_prices import load_ohlc


class SenatePriceLoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.raw={'chart':{'result':[{'meta':{'symbol':'AAA','currency':'USD','exchangeTimezoneName':'America/New_York'},
                   'timestamp':[int(dt.datetime(2025,1,2,14,30,tzinfo=dt.timezone.utc).timestamp())],
                   'indicators':{'quote':[{'open':[100.],'high':[105.],'low':[95.],'close':[102.]}],
                                 'adjclose':[{'adjclose':[51.]}]}}]}}
        self.reviewed={'bars':[{'date':'2025-01-02','open':50.,'close':51.}], 'issues':[]}

    def tearDown(self):self.tmp.cleanup()

    def read(self,changed_hash=False):
        raw=json.dumps(self.raw).encode()
        (self.root/'AAA.raw.json').write_bytes(raw)
        (self.root/'AAA.json').write_text(json.dumps({'symbol':'AAA','rawSha256':'wrong' if changed_hash else hashlib.sha256(raw).hexdigest()}))
        with patch('backend.app.research.lab_engine.senate_prices.price_cache',return_value=self.root),patch('backend.app.research.lab_engine.senate_prices.read_price_history',return_value=self.reviewed):
            return load_ohlc('AAA')

    def test_adjusted_high_low_reconcile_without_changing_quotes(self):
        bar=self.read()['bars']['2025-01-02']
        self.assertEqual((bar['high'],bar['low']),(52.5,47.5))
        self.assertEqual((bar['quote']['high'],bar['quote']['low']),(105.,95.))

    def test_hash_mismatch_and_changed_policy_cache_abstain(self):
        self.assertFalse(self.read(changed_hash=True)['bars'])
        self.reviewed['issues']=['cache_changed_since_source_audit']
        self.assertFalse(self.read()['bars'])

    def test_symbol_currency_and_price_envelope_are_vetoes(self):
        res=self.raw['chart']['result'][0]
        res['meta']['symbol']='WRONG';self.assertFalse(self.read()['bars'])
        res['meta']['symbol']='AAA';res['meta']['currency']='CAD';self.assertFalse(self.read()['bars'])
        res['meta']['currency']='USD';res['indicators']['quote'][0]['high']=[99.];self.assertFalse(self.read()['bars'])

    def test_raw_duplicates_are_rejected_even_if_one_price_is_invalid(self):
        res=self.raw['chart']['result'][0];res['timestamp']*=2
        for values in res['indicators']['quote'][0].values():values.append(None)
        res['indicators']['adjclose'][0]['adjclose'].append(None)
        self.assertFalse(self.read()['bars'])

    def test_unreconciled_adjustment_or_missing_array_abstains(self):
        res=self.raw['chart']['result'][0]
        res['indicators']['adjclose'][0]['adjclose']=[50.]
        self.assertFalse(self.read()['bars'])
        res['indicators']['quote'][0]['high']=[]
        self.assertFalse(self.read()['bars'])
