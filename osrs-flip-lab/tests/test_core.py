import json
from pathlib import Path
import tempfile
import unittest
from core import Ledger, ValidationError, apply_event, check_invariants, new_state, scan_fixture, summary, tax_per_unit

ROOT = Path(__file__).resolve().parents[1]


def place(eid='buy1', side='BUY', qty=100, price=10000, item=1):
    return dict(id=eid, type='place', side=side, item_id=item, name='Test item', quantity=qty, price=price, allowance_checked=True)


def fill(eid, offer, qty, amount, basis='ACTUAL_NET'):
    return dict(id=eid, type='fill', offer_id=offer, quantity=qty, amount=amount, amount_basis=basis)


def action(eid, kind, offer):
    return dict(id=eid, type=kind, offer_id=offer)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.ledger = Ledger(self.path, 'demo')

    def tearDown(self):
        self.temp.cleanup()

    def record(self, event):
        result = self.ledger.record(event)
        check_invariants(result)
        return result

    def acquire(self, qty=100, price=10000):
        self.record(place(qty=qty, price=price))
        self.record(fill('buyfill', 'buy1', qty, qty*price))
        return self.record(action('buycollect', 'collect', 'buy1'))

    def test_tax_thresholds_cap_and_exemption(self):
        self.assertEqual([tax_per_unit(n) for n in [49,50,51,249999999,250000000,999999999]], [0,1,1,4999999,5000000,5000000])
        self.assertEqual(tax_per_unit(1000000000, True), 0)
        self.assertEqual(10300-tax_per_unit(10300)-10000, 94)

    def test_invalid_numeric_types(self):
        for price in [True, 1.1, '100', -1]:
            with self.assertRaises(ValidationError): tax_per_unit(price)

    def test_full_flip_actual_net_no_double_tax(self):
        self.acquire()
        self.record(place('sell1','SELL',100,10300))
        s=self.record(fill('sellfill','sell1',100,1009400))
        self.assertEqual(s['realized_profit'],9400)
        self.assertEqual(s['free_cash'],499000000)
        s=self.record(action('sellcollect','collect','sell1'))
        self.assertEqual(s['free_cash'],500009400)
        self.assertEqual(summary(s)['active_slots'],0)
        self.assertEqual(summary(s)['inventory_cost'],0)

    def test_partial_buy_collect_refunds_price_improvement(self):
        self.record(place())
        self.record(fill('part','buy1',50,499500))
        s=self.record(action('collect1','collect','buy1'))
        self.assertEqual(s['free_cash'],499000500)
        self.assertEqual(s['offers']['buy1']['escrow'],500000)
        self.assertEqual(summary(s)['inventory'][0]['cost'],499500)
        self.record(fill('rest','buy1',50,500000))
        s=self.record(action('collect2','collect','buy1'))
        self.assertEqual(summary(s)['inventory_cost'],999500)

    def test_zero_fill_cancel_requires_collection(self):
        self.record(place())
        s=self.record(action('cancel','cancel','buy1'))
        self.assertEqual(s['free_cash'],499000000)
        self.assertEqual(summary(s)['active_slots'],1)
        s=self.record(action('collect','collect','buy1'))
        self.assertEqual(s['free_cash'],500000000)
        self.assertEqual(s['offers']['buy1']['filled'],0)

    def test_partial_buy_cancel_returns_remainder_not_items_as_cash(self):
        self.record(place())
        self.record(fill('part','buy1',40,400000))
        self.record(action('cancel','cancel','buy1'))
        s=self.record(action('collect','collect','buy1'))
        self.assertEqual(s['free_cash'],499600000)
        self.assertEqual(summary(s)['inventory'][0]['quantity'],40)

    def test_partial_sell_cancel_preserves_cost(self):
        self.acquire(qty=3,price=101)
        self.record(place('sell1','SELL',3,120))
        self.record(fill('part','sell1',1,118))
        self.record(action('cancel','cancel','sell1'))
        s=self.record(action('collect','collect','sell1'))
        self.assertEqual(s['realized_profit'],17)
        self.assertEqual(summary(s)['inventory'][0]['quantity'],2)
        self.assertEqual(summary(s)['inventory_cost'],202)

    def test_sell_requires_collected_inventory(self):
        self.record(place())
        self.record(fill('fill','buy1',100,1000000))
        with self.assertRaises(ValidationError): self.record(place('sell','SELL',100,10300))

    def test_idempotent_repeated_event(self):
        self.record(place())
        event=fill('part','buy1',20,200000)
        first=self.record(event)
        second=self.record(event)
        self.assertEqual(first,second)
        self.assertEqual(len(self.ledger.export()['events']),2)

    def test_conflicting_id_is_rejected(self):
        self.record(place())
        with self.assertRaises(ValidationError): self.record(place(qty=200))

    def test_oversized_fill_rolls_back(self):
        s=self.record(place())
        with self.assertRaises(ValidationError): self.record(fill('bad','buy1',101,1010000))
        self.assertEqual(self.ledger.get(),s)
        self.assertEqual(len(self.ledger.export()['events']),1)

    def test_actual_net_basis_required(self):
        self.acquire()
        self.record(place('sell','SELL'))
        with self.assertRaises(ValidationError): self.record(fill('wrong','sell',1,10000,'GROSS'))

    def test_new_identical_offer_has_distinct_attempt(self):
        self.record(place())
        self.record(action('cancel','cancel','buy1'))
        self.record(action('collect','collect','buy1'))
        s=self.record(place('buy2'))
        self.assertEqual(len(s['offers']),2)

    def test_slot_limit(self):
        for i in range(8): self.record(place(str(i),qty=1,price=1,item=i+1))
        with self.assertRaises(ValidationError): self.record(place('ninth',qty=1,price=1))

    def test_exposure_limit(self):
        with self.assertRaises(ValidationError): self.record(place(qty=10000,price=10000))

    def test_unknown_allowance_blocks_buy_record(self):
        event=place();event['allowance_checked']=False
        with self.assertRaises(ValidationError): self.record(event)

    def test_database_restart(self):
        s=self.acquire()
        other=Ledger(self.path,'demo')
        self.assertEqual(other.get(),s)

    def test_demo_journal_isolation(self):
        with self.assertRaises(ValidationError): Ledger(self.path,'journal')

    def test_backup_restore_replays_all_events(self):
        self.acquire()
        original=self.ledger.export()
        restored=Ledger.restore(Path(self.temp.name)/'restored.sqlite3', original)
        self.assertEqual(restored.export(),original)

    def test_corrupted_backup_is_rejected_before_creation(self):
        self.acquire()
        backup=self.ledger.export();backup['state']['free_cash']+=1
        dest=Path(self.temp.name)/'bad.sqlite3'
        with self.assertRaises(ValidationError): Ledger.restore(dest,backup)
        self.assertFalse(dest.exists())

    def test_backup_never_overwrites(self):
        with self.assertRaises(ValidationError): Ledger.restore(self.path,self.ledger.export())

    def test_fixture_scanner_filters_stale_and_nonpositive(self):
        fixture=json.loads((ROOT/'fixtures/market.json').read_text())
        rows=scan_fixture(fixture,new_state())
        self.assertEqual(len(rows),4)
        self.assertFalse({900005,900006}&{r['item_id'] for r in rows})
        self.assertLessEqual(sum(r['cost'] for r in rows),50000000)

    def test_fixture_scanner_respects_remaining_cash_and_slots(self):
        fixture=json.loads((ROOT/'fixtures/market.json').read_text())
        s=new_state(cash=500000)
        for i in range(7): s=apply_event(s,place(str(i),qty=1,price=1))
        rows=scan_fixture(fixture,s)
        self.assertLessEqual(len(rows),1)
        self.assertLessEqual(sum(r['cost'] for r in rows),s['free_cash'])

    def test_future_quotes_rejected(self):
        fixture=json.loads((ROOT/'fixtures/market.json').read_text())
        fixture['items']=fixture['items'][:1]
        fixture['items'][0]['high_time']=fixture['as_of']+1
        self.assertEqual(scan_fixture(fixture,new_state()),[])


if __name__ == '__main__': unittest.main()
