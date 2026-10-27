from pathlib import Path
import tempfile
import unittest
from core import Ledger
from demo_research import seed_demo
from research import build_report,inventory_valuation

class ValuationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.l=Ledger(self.path/'l.sqlite3','demo');self.store,rules,now=seed_demo(self.path/'m.sqlite3')
        self.report=build_report(self.store.snapshot(now),rules)
    def tearDown(self):self.temp.cleanup()
    def place(self,id,side='BUY',item=900101,qty=10,price=10000):
        self.l.record(dict(id=id,type='place',side=side,item_id=item,name='Test',quantity=qty,price=price,allowance_checked=True))
    def action(self,id,type,offer,**kwargs):self.l.record(dict(id=id,type=type,offer_id=offer,**kwargs))
    def test_pending_collected_reserved_counted_once(self):
        self.place('b');self.action('f','fill','b',quantity=10,amount=100000)
        v=inventory_valuation(self.l.get(),self.report)
        self.assertEqual(v['positions'][0]['quantity'],10);self.assertEqual(v['reference_portfolio_net'],-2000)
        self.action('c','collect','b');self.place('s',side='SELL',price=10400)
        w=inventory_valuation(self.l.get(),self.report)
        self.assertEqual(w['positions'][0]['quantity'],10);self.assertEqual(w['reference_portfolio_net'],-2000)
    def test_unpriced_items_do_not_become_zero(self):
        self.place('b',item=12345);self.action('f','fill','b',quantity=10,amount=100000)
        v=inventory_valuation(self.l.get(),self.report)
        self.assertEqual(v['unpriced_positions'],1);self.assertIsNone(v['reference_equity'])
    def test_stale_source_does_not_produce_liquidation_promise(self):
        self.place('b',item=900104,price=1000);self.action('f','fill','b',quantity=10,amount=10000)
        v=inventory_valuation(self.l.get(),self.report)
        self.assertIsNone(v['reference_equity'])
    def test_cash_only_matches_recorded_cash(self):
        self.assertEqual(inventory_valuation(self.l.get(),self.report)['reference_equity'],500000000)
    def test_partial_sale_preserves_total_items(self):
        self.place('b');self.action('f','fill','b',quantity=10,amount=100000);self.action('c','collect','b')
        self.place('s',side='SELL',price=10400);self.action('sf','fill','s',quantity=4,amount=40768,amount_basis='ACTUAL_NET')
        v=inventory_valuation(self.l.get(),self.report)
        self.assertEqual(v['positions'][0]['quantity'],6)
        self.assertEqual(v['reference_portfolio_net'],-432)
