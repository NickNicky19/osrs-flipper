from pathlib import Path
import tempfile
import unittest
from app import ROOT
from core import Ledger,ValidationError
from service import ResearchService

class DecisionLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.l=Ledger(Path(self.temp.name)/'l.sqlite3','demo');self.s=ResearchService(self.l,ROOT)
        r=self.s.report();self.s.freeze(dict(id='d1',report_hash=r['report_hash'],item_id=900101,buy=10000,sell=10400,quantity=10))
    def tearDown(self):self.temp.cleanup()
    def event(self):return dict(id='p1',type='place',side='BUY',item_id=900101,name='Test',quantity=1,price=10000,allowance_checked=True,decision_id='d1')
    def test_verified_link_preserved(self):
        e=self.event();self.s.validate_link(e);self.l.record(e)
        self.assertEqual(self.l.get()['offers']['p1']['decision_id'],'d1');self.l.verify_integrity()
    def test_different_item_rejected(self):
        e=self.event();e['item_id']=1
        with self.assertRaises(ValidationError):self.s.validate_link(e)
    def test_missing_receipt_rejected(self):
        e=self.event();e['decision_id']='missing'
        with self.assertRaises(ValidationError):self.s.validate_link(e)
    def test_actual_price_deviation_not_hidden(self):
        e=self.event();e['price']=10001;self.s.validate_link(e);self.l.record(e)
        self.assertEqual(self.l.get()['offers']['p1']['price'],10001)
    def test_malformed_link_rejected_before_write(self):
        e=self.event();e['decision_id']=[]
        with self.assertRaises(ValidationError):self.l.record(e)
        self.assertFalse(self.l.get()['offers'])
