from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import time
from core import Ledger,ValidationError,new_state,tax_per_unit
from demo_research import seed_demo,DEMO_NOW
from market import SourceContract,MarketStore,canonical
from research import build_report,explore,break_even_price,price_grid,journal_evidence,ScreenPolicy
from service import ResearchService
from collector import collect_once

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.s,self.rules,self.now=seed_demo(self.path/'demo.sqlite3')
        self.snapshot=self.s.snapshot(self.now)
        self.report=build_report(self.snapshot,self.rules)
    def tearDown(self):self.temp.cleanup()
    def row(self,id=900101):return next(r for r in self.report['rows'] if r['item_id']==id)
    def test_three_candidates_and_three_exclusions(self):
        self.assertEqual(sum(not r['flags'] for r in self.report['rows']),3)
    def test_stale_side_trap(self):
        self.assertIn('LOW_QUOTE_STALE',self.row(900104)['flags'])
    def test_missing_intervals_trap(self):
        self.assertIn('INSUFFICIENT_INTERVAL_COVERAGE',self.row(900105)['flags'])
    def test_tax_erased_spread(self):
        self.assertIn('NO_POSITIVE_AFTER_TAX_SPREAD',self.row(900106)['flags'])
    def test_unknown_tax_rule_disables_scenarios(self):
        result=build_report(self.snapshot,{})
        self.assertTrue(all(r['flags'] and not r['scenarios'] for r in result['rows']))
    def test_expired_rule_disabled(self):
        self.rules['900101']['expires_at']=self.now
        r=build_report(self.snapshot,self.rules)
        row=next(x for x in r['rows'] if x['item_id']==900101)
        self.assertIn('ITEM_RULE_EXPIRED_OR_FUTURE',row['flags'])
    def test_new_fetch_does_not_fix_stale_quote(self):
        self.assertEqual(self.row(900104)['low_age'],1800)
        self.assertEqual(self.report['source_status'],'SYNTHETIC_REPLAY')
    def test_public_unreviewed_blocks(self):
        self.snapshot['namespace']='public';self.snapshot['latest']['contract']['reviewed']=False
        report=build_report(self.snapshot,self.rules)
        self.assertIn('SOURCE_REVIEW_NOT_RECORDED',report['global_flags'])
        self.assertTrue(all(r['flags'] for r in report['rows']))
    def test_outage_does_not_disappear_existing_observations(self):
        self.snapshot['namespace']='public';self.snapshot['attempts'].append(dict(kind='latest',ok=0))
        r=build_report(self.snapshot,self.rules)
        self.assertEqual(len(r['rows']),6);self.assertIn('LAST_LATEST_FETCH_FAILED',r['global_flags'])
    def test_report_hash_reproducible(self):
        self.assertEqual(self.report['report_hash'],build_report(self.snapshot,self.rules)['report_hash'])
    def test_policy_change_changes_hash(self):
        self.assertNotEqual(self.report['report_hash'],build_report(self.snapshot,self.rules,ScreenPolicy(min_coverage_bps=9000))['report_hash'])
    def test_exact_breakeven_exhaustive_small_range(self):
        for buy in range(1,3000):
            for ex in (False,True):
                b=break_even_price(buy,ex)
                self.assertGreaterEqual(b-tax_per_unit(b,ex),buy)
                self.assertLess(b-1-tax_per_unit(b-1,ex),buy)
    def test_breakeven_cap_boundary(self):
        for buy in (244999999,245000000,245000001,250000000,500000000):
            b=break_even_price(buy,False)
            self.assertGreaterEqual(b-tax_per_unit(b),buy)
            self.assertLess(b-1-tax_per_unit(b-1),buy)
    def test_price_grid_is_not_one_gp_only(self):
        grid=price_grid(10000,10400,False)
        self.assertEqual([s['buy'] for s in grid],[10000,10040,10100])
    def test_excluded_row_cannot_be_used(self):
        with self.assertRaises(ValidationError):explore(self.row(900104),1000,1700,1,new_state())
    def test_scenario_has_no_invented_probability(self):
        s=explore(self.row(),10000,10400,100,new_state())
        self.assertIsNone(s['fill_probability']);self.assertIsNone(s['expected_profit']);self.assertIsNone(s['recommended_quantity'])
    def test_exact_conditional_net(self):
        s=explore(self.row(),10000,10400,100,new_state())
        self.assertEqual(s['conditional_net'],19200)
    def test_tax_exempt_scenario(self):
        s=explore(self.row(900102),580,593,100,new_state())
        self.assertEqual(s['conditional_net'],1300)
    def test_stresses_can_turn_winner_into_loser(self):
        s=explore(self.row(),10000,10400,100,new_state())
        self.assertGreater(s['conditional_net'],0)
        self.assertLess(s['stress_scenarios'][-1]['conditional_net'],0)
    def test_volume_ratio_not_capacity(self):
        s=explore(self.row(),10000,10400,999,new_state())
        self.assertGreater(s['entry_share_of_observed_units_bps'],0)
        self.assertEqual(s['sizing_status'],'NOT_ACCOUNT_QUALIFIED')
    def test_unknown_allowance_always_blocks_qualification(self):
        s=explore(self.row(),10000,10400,1,new_state())
        self.assertIn('ACCOUNT_REMAINING_ALLOWANCE_UNKNOWN',s['constraint_flags'])
    def test_50m_cap_not_relaxed(self):
        s=explore(self.row(),10000,10400,6000,new_state())
        self.assertIn('EXPOSURE_CEILING_EXCEEDED',s['constraint_flags'])
    def test_public_limit_not_account_allowance(self):
        s=explore(self.row(),10000,10400,10001,new_state())
        self.assertIn('QUANTITY_EXCEEDS_PUBLIC_ITEM_LIMIT',s['constraint_flags'])
        self.assertIn('ACCOUNT_REMAINING_ALLOWANCE_UNKNOWN',s['constraint_flags'])
    def test_no_cash_double_reservation(self):
        state=new_state(10000);s=explore(self.row(),10000,10400,2,state)
        self.assertIn('INSUFFICIENT_RECORDED_CASH',s['constraint_flags'])
    def test_booleans_rejected_in_scenario(self):
        with self.assertRaises(ValidationError):explore(self.row(),10000,10400,True,new_state())
    def test_source_no_data_is_explicit(self):
        s=MarketStore(self.path/'empty.sqlite3')
        r=build_report(s.snapshot(self.now),{})
        self.assertEqual(r['rows'],[]);self.assertIn('NO_QUOTE_CAPTURE',r['global_flags'])

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.ledger=Ledger(self.path/'demo.sqlite3','demo')
        self.service=ResearchService(self.ledger,self.path)
        self.r=self.service.report()
    def tearDown(self):self.temp.cleanup()
    def test_freeze_is_not_trade(self):
        self.service.freeze(dict(id='r1',report_hash=self.r['report_hash']))
        self.assertEqual(len(self.ledger.get()['offers']),0)
        self.assertEqual(self.service.evidence()['saved_research_receipts'],1)
    def test_forged_hash_rejected(self):
        with self.assertRaises(ValidationError):self.service.freeze(dict(id='r1',report_hash='f'*64))
    def test_account_change_requires_refresh(self):
        self.ledger.record(dict(id='p1',type='place',side='BUY',item_id=1,name='test',quantity=1,price=100,allowance_checked=True))
        with self.assertRaises(ValidationError):self.service.freeze(dict(id='r1',report_hash=self.r['report_hash']))
    def test_scenario_is_recomputed_not_client_claim(self):
        result=self.service.scenario(dict(report_hash=self.r['report_hash'],item_id=900101,buy=10000,sell=10400,quantity=10,conditional_net=999999))
        self.assertEqual(result['conditional_net'],1920)
    def test_journal_attempt_denominator_keeps_zero_fill(self):
        for i in range(4):
            self.ledger.record(dict(id=f'p{i}',type='place',side='BUY',item_id=1,name='test',quantity=1,price=100,allowance_checked=True))
            if i==0:self.ledger.record(dict(id='fill',type='fill',offer_id=f'p{i}',quantity=1,amount=100))
            else:self.ledger.record(dict(id=f'c{i}',type='cancel',offer_id=f'p{i}'))
            self.ledger.record(dict(id=f'g{i}',type='collect',offer_id=f'p{i}'))
        e=self.service.evidence()['by_side']['BUY']
        self.assertEqual(e['attempts'],4);self.assertEqual(e['filled_in_full'],1);self.assertEqual(e['cancelled_zero_fill'],3)
    def test_demo_cannot_load_public_market_db(self):
        public=MarketStore(self.path/'market.sqlite3')
        self.assertNotEqual(self.service.store.namespace,public.namespace)

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=MarketStore(Path(self.temp.name)/'m.sqlite3')
        now=int(time.time());self.c=SourceContract(reviewed=True,reviewed_at=now-10,expires_at=now+100,
            permission_reference='TEST_ONLY',semantics_reference='TEST_ONLY',user_agent='TEST_ONLY')
    def tearDown(self):self.temp.cleanup()
    def test_unreviewed_collection_blocks_before_network(self):
        calls=[]
        with self.assertRaises(ValidationError):collect_once(self.s,SourceContract(),fetcher=lambda *a:calls.append(a))
        self.assertFalse(calls)
    def test_probe_does_not_authorize_source(self):
        def fake(k,c):return b'[{"id":1,"name":"Test","members":true}]',dict(test=True)
        result=collect_once(self.s,SourceContract(),('mapping',),probe=True,fetcher=fake)
        self.assertEqual(result['status'],'SCHEMA_CAPTURED_REVIEW_REQUIRED')
        self.assertFalse(self.s.snapshot()['mapping']['contract']['reviewed'])
    def test_network_failure_stops_remaining_calls(self):
        calls=[]
        def fail(k,c):calls.append(k);raise OSError('test network down')
        result=collect_once(self.s,self.c,fetcher=fail)
        self.assertEqual(len(calls),1);self.assertEqual(result['status'],'BLOCKED')
    def test_schema_failure_stops_remaining_calls(self):
        calls=[]
        def fail(k,c):calls.append(k);return b'{}',{}
        result=collect_once(self.s,self.c,fetcher=fail)
        self.assertEqual(len(calls),1);self.assertEqual(result['status'],'BLOCKED')
