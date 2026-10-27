import unittest
from simulator import replay


def bar(item,when,low,high,low_units=50,high_units=50):
    return dict(item_id=item,bucket_start=when,received_at=when+300,
                low_avg=low,high_avg=high,low_units=low_units,high_units=high_units)


class SimulatorTests(unittest.TestCase):
    def test_sequential_partials_tax_collection_and_conservation(self):
        plan=dict(id='p1',item_id=1,issued_at=1000,entry_deadline=2400,exit_deadline=3000,
                  buy=100,sell=120,quantity=10,public_limit=10,tax_exempt=False)
        result=replay([plan],[bar(1,1800,100,110),bar(1,2100,100,120),
                              bar(1,2400,110,120),bar(1,2700,110,120)],
                      capital=10_000)
        self.assertEqual(result['status'],'HISTORICAL_SIMULATION')
        self.assertEqual(result['positions'][0]['modeled_bought'],10)
        self.assertEqual(result['positions'][0]['modeled_sold'],10)
        self.assertEqual(result['realized_after_tax_gp'],180)
        self.assertEqual(result['cash_gp']+result['claimable_gp']+result['open_stock_cost_gp'],10_180)
        self.assertEqual(result['positions'][0]['remaining'],0)

    def test_no_contact_does_not_create_fills_or_profit(self):
        plan=dict(id='p1',item_id=1,issued_at=1000,entry_deadline=2100,exit_deadline=3000,
                  buy=100,sell=120,quantity=10,public_limit=10,tax_exempt=False)
        result=replay([plan],[bar(1,1800,101,119),bar(1,2100,101,119),bar(1,2400,101,119),bar(1,2700,101,119)],capital=10_000)
        self.assertEqual(result['positions'][0]['modeled_bought'],0)
        self.assertEqual(result['realized_after_tax_gp'],0)
        self.assertEqual(result['cash_gp'],10_000)

    def test_unsold_inventory_keeps_cost_and_uncertain_mark_separate(self):
        plan=dict(id='p1',item_id=1,issued_at=1000,entry_deadline=2400,exit_deadline=2400,
                  buy=100,sell=120,quantity=10,public_limit=10,tax_exempt=False)
        result=replay([plan],[bar(1,1800,100,110),bar(1,2100,100,110)],capital=10_000)
        self.assertEqual(result['positions'][0]['modeled_bought'],10)
        self.assertEqual(result['positions'][0]['modeled_sold'],0)
        self.assertEqual(result['open_stock_cost_gp'],1_000)
        self.assertEqual(result['realized_after_tax_gp'],0)
        self.assertEqual(result['cash_gp'],9_000)
        self.assertEqual(result['uncertain_open_stock_reference_mark_gp'],1_000)

    def test_unsupported_shared_limit_group_fails_closed(self):
        plan=dict(id='p1',item_id=1,issued_at=1000,entry_deadline=2100,exit_deadline=2400,buy=100,sell=120,
                  quantity=10,public_limit=10,tax_exempt=False,limit_group_status='UNKNOWN')
        result=replay([plan],[bar(1,1800,100,110)])
        self.assertEqual(result['status'],'DATA_INSUFFICIENT')

    def test_fractional_bucket_averages_and_missing_window_are_explicit(self):
        plan=dict(id='p1',item_id=1,issued_at=1000,entry_deadline=2100,exit_deadline=2400,
                  buy=100,sell=120,quantity=10,public_limit=10,tax_exempt=False)
        valid=replay([plan],[bar(1,1800,99.5,110.25),bar(1,2100,100.5,120.5)],capital=10_000)
        self.assertEqual(valid['status'],'HISTORICAL_SIMULATION')
        self.assertEqual(valid['positions'][0]['modeled_bought'],5)
        self.assertEqual(valid['positions'][0]['modeled_sold'],5)
        self.assertEqual(valid['realized_after_tax_gp'],90)
        missing=replay([plan],[bar(1,2100,99.5,120.5)],capital=10_000)
        self.assertEqual(missing['status'],'DATA_INSUFFICIENT')
        self.assertEqual(missing['reason'],'INCOMPLETE_ITEM_ACTION_WINDOW')
        self.assertEqual(replay([plan],[None])['status'],'DATA_INSUFFICIENT')

    def test_late_verified_publication_yields_zero_action_in_matched_policy(self):
        plan=dict(id='late',item_id=1,issued_at=1000,activation_at=2000,
                  entry_deadline=1800,exit_deadline=2700,buy=100,sell=120,
                  quantity=10,public_limit=10,tax_exempt=False)
        result=replay([plan],[bar(1,2100,100,120)],capital=10_000,
                      publication_delay=0,action_delay=0,allow_expired_no_action=True)
        self.assertEqual(result['status'],'HISTORICAL_SIMULATION')
        self.assertEqual(result['positions'][0]['status'],'EXPIRED_BEFORE_ACTION')
        self.assertEqual((result['modeled_bought_units'],result['modeled_sold_units']),(0,0))
        self.assertEqual(result['cash_gp'],10_000)


if __name__=='__main__':unittest.main()
