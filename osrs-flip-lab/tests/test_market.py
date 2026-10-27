import gzip
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from core import ValidationError
from market import MarketStore,SourceContract,parse_payload,strict_json,canonical,aggregate_item
from demo_research import DEMO_NOW,seed_demo

N=DEMO_NOW
C=SourceContract(reviewed=True,reviewed_at=N-10000,expires_at=N+10000,
    permission_reference='TEST_POLICY',semantics_reference='TEST_BUCKET_START',user_agent='test contact')

def raw(v):return canonical(v).encode()
def bar(start,low=10,high=20,ident='1'):
    return dict(timestamp=start,data={ident:dict(avgLowPrice=100,avgHighPrice=110,lowPriceVolume=low,highPriceVolume=high)})
def latest(low=100,high=110):
    return dict(data={'1':dict(low=low,high=high,lowTime=N-20,highTime=N-10)})

class ContractTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_keys(self):
        with self.assertRaises(ValidationError):strict_json(b'{"a":1,"a":2}')
    def test_strict_json_rejects_nonfinite(self):
        for v in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValidationError):strict_json('{"a":'+v+'}')
    def test_mapping_null_limit_is_unknown(self):
        p=parse_payload('mapping',raw([dict(id=1,name='Test',members=True)]),N,C)
        self.assertIsNone(p['items']['1']['public_limit'])
    def test_mapping_boolean_id_rejected(self):
        with self.assertRaises(ValidationError):parse_payload('mapping',raw([dict(id=True,name='Test',members=True)]),N,C)
    def test_mapping_duplicate_rejected(self):
        v=dict(id=1,name='Test',members=True)
        with self.assertRaises(ValidationError):parse_payload('mapping',raw([v,v]),N,C)
    def test_quote_nullability(self):
        v=latest();v['data']['1']['lowTime']=None
        with self.assertRaises(ValidationError):parse_payload('latest',raw(v),N,C)
    def test_null_side_preserved(self):
        v=latest();v['data']['1']['low']=None;v['data']['1']['lowTime']=None
        self.assertIsNone(parse_payload('latest',raw(v),N,C)['items']['1']['low'])
    def test_future_quote_rejected(self):
        v=latest();v['data']['1']['highTime']=N+1
        with self.assertRaises(ValidationError):parse_payload('latest',raw(v),N,C)
    def test_boolean_volume_rejected(self):
        with self.assertRaises(ValidationError):parse_payload('5m',raw(bar(N-300,True)),N,C)
    def test_negative_volume_rejected(self):
        with self.assertRaises(ValidationError):parse_payload('5m',raw(bar(N-300,-1)),N,C)
    def test_nonzero_volume_requires_price(self):
        v=bar(N-300);v['data']['1']['avgLowPrice']=None
        with self.assertRaises(ValidationError):parse_payload('5m',raw(v),N,C)
    def test_null_volume_preserved(self):
        v=bar(N-300,None);v['data']['1']['avgLowPrice']=None
        self.assertIsNone(parse_payload('5m',raw(v),N,C)['items']['1']['low_units'])
    def test_incomplete_bar_rejected(self):
        with self.assertRaises(ValidationError):parse_payload('5m',raw(bar(N)),N,C)
    def test_unaligned_bucket_rejected(self):
        with self.assertRaises(ValidationError):parse_payload('5m',raw(bar(N-299)),N,C)
    def test_end_convention_explicit(self):
        p=parse_payload('5m',raw(bar(N)),N,replace(C,bucket_timestamp='end'))
        self.assertEqual(p['start'],N-300)
    def test_false_text_not_review(self):
        with self.assertRaises(ValidationError):SourceContract.from_dict({'reviewed':'false'})
    def test_expired_and_future_review_blocked(self):
        self.assertTrue(replace(C,expires_at=N).blockers(N))
        self.assertTrue(replace(C,reviewed_at=N+1).blockers(N))
    def test_missing_reference_blocks(self):
        self.assertTrue(replace(C,semantics_reference='').blockers(N))

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'market.sqlite3';self.s=MarketStore(self.path)
    def tearDown(self):self.temp.cleanup()
    def test_no_namespace_mix(self):
        with self.assertRaises(ValidationError):MarketStore(self.path,'fixture')
    def test_repeat_does_not_add_volume(self):
        for stamp in (N,N+1,N+2):self.s.ingest('5m',raw(bar(N-300)),stamp,C)
        snap=self.s.snapshot(N+3,900);a=aggregate_item(snap,1)
        self.assertEqual(a['low_units'],10);self.assertEqual(len(snap['bars']),1)
        self.assertEqual(len(self.s.export()['attempts']),3)
    def test_revision_asof_no_future_leak(self):
        self.s.ingest('5m',raw(bar(N-300,10)),N,C)
        self.s.ingest('5m',raw(bar(N-300,999)),N+50,C)
        self.assertEqual(aggregate_item(self.s.snapshot(N+49,900),1)['low_units'],10)
        self.assertEqual(aggregate_item(self.s.snapshot(N+50,900),1)['low_units'],999)
    def test_revision_removing_item_does_not_fall_back(self):
        self.s.ingest('5m',raw(bar(N-300)),N,C)
        self.s.ingest('5m',raw(dict(timestamp=N-300,data={})),N+1,C)
        a=aggregate_item(self.s.snapshot(N+2,900),1)
        self.assertEqual(a['observed_intervals'],0)
    def test_gap_not_zero(self):
        self.s.ingest('5m',raw(bar(N-300)),N,C)
        a=aggregate_item(self.s.snapshot(N,900),1)
        self.assertEqual(a['observed_intervals'],1);self.assertEqual(a['expected_intervals'],3)
        self.assertEqual(a['coverage_bps'],3333)
    def test_explicit_zero_counts_for_coverage(self):
        self.s.ingest('5m',raw(bar(N-300,0,0)),N,C)
        a=aggregate_item(self.s.snapshot(N,900),1)
        self.assertEqual(a['low_intervals'],1);self.assertEqual(a['low_units'],0)
    def test_null_volume_not_known_zero(self):
        v=bar(N-300,None);v['data']['1']['avgLowPrice']=None
        self.s.ingest('5m',raw(v),N,C)
        a=aggregate_item(self.s.snapshot(N,900),1)
        self.assertEqual(a['low_intervals'],0)
    def test_hour_ago_bar_not_current_window(self):
        self.s.ingest('5m',raw(bar(N-3900,9999)),N-3600,C)
        self.assertEqual(aggregate_item(self.s.snapshot(N,3600),1)['low_units'],0)
    def test_available_at_applies_to_late_capture(self):
        self.s.ingest('5m',raw(bar(N-600,999)),N+10,C)
        self.assertEqual(len(self.s.snapshot(N,900)['bars']),0)
    def test_invalid_capture_quarantined(self):
        r=self.s.ingest('latest',b'{"data": false}',N,C)
        self.assertFalse(r['ok']);self.assertIsNone(self.s.snapshot(N)['latest'])
        self.assertEqual(len(self.s.export()['captures']),1)
    def test_quarantine_revalidation_never_backdates(self):
        v=raw(bar(N));self.assertFalse(self.s.ingest('5m',v,N,C)['ok'])
        self.assertTrue(self.s.ingest('5m',v,N+300,C)['ok'])
        self.assertEqual(len(self.s.snapshot(N,900)['bars']),0)
        self.assertEqual(len(self.s.snapshot(N+300,900)['bars']),1)
        self.assertEqual(len(self.s.export()['captures']),2)

    def test_response_a_b_a_uses_last_observed_revision(self):
        self.s.ingest('5m',raw(bar(N-300,10)),N,C)
        self.s.ingest('5m',raw(bar(N-300,99)),N+10,C)
        self.s.ingest('5m',raw(bar(N-300,10)),N+20,C)
        self.assertEqual(aggregate_item(self.s.snapshot(N+15,900),1)['low_units'],99)
        self.assertEqual(aggregate_item(self.s.snapshot(N+21,900),1)['low_units'],10)
        self.assertEqual(len(self.s.export()['captures']),2)

    def test_confirmation_does_not_rewrite_original_availability(self):
        self.s.ingest('latest',raw(latest()),N,C)
        self.s.ingest('latest',raw(latest()),N+100,C)
        a=self.s.snapshot(N+50)['latest'];b=self.s.snapshot(N+100)['latest']
        self.assertEqual(a['received_at'],N);self.assertEqual(b['received_at'],N)
        self.assertEqual(a['confirmed_at'],N);self.assertEqual(b['confirmed_at'],N+100)
        self.assertEqual(b['data']['items']['1']['low_time'],N-20)

    def test_quote_corruption_detected(self):
        self.s.ingest('latest',raw(latest()),N,C)
        with self.s.connect() as db:db.execute('UPDATE captures SET digest=?',('f'*64,))
        with self.assertRaises(ValidationError):self.s.snapshot(N)
    def test_bucket_index_corruption_detected(self):
        self.s.ingest('5m',raw(bar(N-300)),N,C)
        with self.s.connect() as db:db.execute('UPDATE captures SET bucket_start=?',(N-600,))
        with self.assertRaises(ValidationError):self.s.snapshot(N,900)
    def test_replay_survives_restart(self):
        self.s.ingest('latest',raw(latest()),N,C)
        self.assertEqual(MarketStore(self.path).snapshot(N)['latest']['data']['items']['1']['low'],100)
    def test_weighted_prices(self):
        self.s.ingest('5m',raw(bar(N-600,10)),N-300,C)
        v=bar(N-300,30);v['data']['1']['avgLowPrice']=200
        self.s.ingest('5m',raw(v),N,C)
        self.assertEqual(aggregate_item(self.s.snapshot(N,900),1)['low_weighted_price'],175)
    def test_no_overlap_from_revisions(self):
        for x in range(1,10):self.s.ingest('5m',raw(bar(N-300,x)),N+x,C)
        self.assertEqual(aggregate_item(self.s.snapshot(N+20,900),1)['low_units'],9)
    def test_freeze_idempotent_and_conflicts(self):
        b=dict(namespace='public',result=1)
        self.s.freeze('d1',b,N);self.s.freeze('d1',b,N+1)
        self.assertEqual(len(self.s.decisions()),1)
        with self.assertRaises(ValidationError):self.s.freeze('d1',dict(namespace='public',result=2),N+2)
    def test_receipt_corruption_detected(self):
        self.s.freeze('d1',dict(namespace='public'),N)
        with self.s.connect() as db:db.execute("UPDATE decisions SET body='{}'")
        with self.assertRaises(ValidationError):self.s.decisions()
    def test_freeze_namespace_enforced(self):
        with self.assertRaises(ValidationError):self.s.freeze('d1',dict(namespace='fixture'),N)

class RandomizedRevisionTests(unittest.TestCase):
    def test_revisions_match_an_independent_interval_dictionary(self):
        import random
        rng=random.Random(62183)
        with tempfile.TemporaryDirectory() as tmp:
            store=MarketStore(Path(tmp)/'m.sqlite3');history=[]
            for i in range(100):
                bucket=N-300*rng.randint(1,12);units=rng.randint(0,10000);stamp=N+i
                store.ingest('5m',raw(bar(bucket,units)),stamp,C)
                history.append((stamp,bucket,units))
            for cutoff in (N+9,N+31,N+60,N+99):
                expected={}
                for stamp,bucket,units in history:
                    if stamp<=cutoff:expected[bucket]=units
                actual=aggregate_item(store.snapshot(cutoff,3600),1)
                self.assertEqual(actual['low_units'],sum(expected.values()))
                self.assertEqual(actual['observed_intervals'],len(expected))
