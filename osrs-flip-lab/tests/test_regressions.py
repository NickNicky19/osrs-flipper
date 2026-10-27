"""Focused v0.2 regressions; these are software checks, not market evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from core import Ledger, ValidationError, check_invariants, new_state, scan_fixture, summary, tax_per_unit

ROOT = Path(__file__).resolve().parents[1]


def buy(**changes):
    event = dict(id='buy', type='place', side='BUY', item_id=1, name='Synthetic test',
                 quantity=10, price=100, allowance_checked=True)
    event.update(changes)
    return event


def factual(**changes):
    event = buy(record_external_action=True,
                exception_reason='This manual offer already happened; retain an accurate record.')
    event.update(changes)
    return event


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'journal.sqlite3'
        self.ledger = Ledger(self.path, 'journal')

    def tearDown(self):
        self.tmp.cleanup()

    def test_exemption_requires_a_real_boolean(self):
        for value in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                tax_per_unit(10300, value)
        self.assertEqual(tax_per_unit(10300, False), 206)
        self.assertEqual(tax_per_unit(10300, True), 0)

    def test_impossible_buy_cash_rejected_atomically(self):
        before = self.ledger.record(buy())
        with self.assertRaises(ValidationError):
            self.ledger.record(dict(id='fill', type='fill', offer_id='buy', quantity=10, amount=1))
        self.assertEqual(self.ledger.get(), before)
        self.assertEqual(len(self.ledger.export()['events']), 1)

    def test_malformed_event_id_has_validation_error_not_sql_error(self):
        for value in ([], {}, 1, None, '', 'a' * 81):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.ledger.record(buy(id=value))
        self.assertEqual(self.ledger.export()['events'], [])

    def test_nonobject_event_is_rejected(self):
        for value in (None, [], 'data', 2):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.ledger.record(value)

    def test_malformed_offer_id_is_rejected(self):
        self.ledger.record(buy())
        for value in ([], {}, None, ''):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.ledger.record(dict(id='collect', type='collect', offer_id=value))

    def test_invalid_mode_rejected_before_creation(self):
        dest = Path(self.tmp.name) / 'invalid.sqlite3'
        with self.assertRaises(ValidationError):
            Ledger(dest, 'live')
        self.assertFalse(dest.exists())

    def test_already_executed_over_cap_is_recorded_not_approved(self):
        s = self.ledger.record(factual(quantity=6000, price=10000))
        self.assertEqual(s['exposure_cap'], 50_000_000)
        self.assertEqual(summary(s)['committed_at_cost'], 60_000_000)
        self.assertTrue(summary(s)['exposure_over_cap'])
        self.assertIn('EXPOSURE_CEILING_EXCEEDED', s['offers']['buy']['recording_warnings'])
        fixture = json.loads((ROOT / 'fixtures/market.json').read_text())
        self.assertEqual(scan_fixture(fixture, s), [])

    def test_factual_unknown_allowance_keeps_warning(self):
        s = self.ledger.record(factual(allowance_checked=False))
        self.assertIn('ALLOWANCE_NOT_VERIFIED', s['offers']['buy']['recording_warnings'])
        self.assertEqual(len(summary(s)['recording_warnings']), 1)
        self.assertEqual(s['exposure_cap'], 50_000_000)

    def test_exception_needs_both_acknowledgement_and_reason(self):
        examples = [factual(allowance_checked=False, record_external_action=False),
                    factual(allowance_checked=False, exception_reason=''),
                    factual(allowance_checked=False, exception_reason=None),
                    factual(allowance_checked=False, exception_reason='a' * 501)]
        for e in examples:
            with self.subTest(event=e), self.assertRaises(ValidationError):
                self.ledger.record(e)
        self.assertEqual(self.ledger.export()['events'], [])

    def test_exception_acknowledgement_must_be_boolean(self):
        with self.assertRaises(ValidationError):
            self.ledger.record(factual(allowance_checked=False, record_external_action='true'))

    def test_exception_cannot_create_cash(self):
        with self.assertRaises(ValidationError):
            self.ledger.record(factual(quantity=60000, price=10000))
        self.assertEqual(self.ledger.get()['free_cash'], 500_000_000)

    def test_exception_cannot_create_an_extra_slot(self):
        for i in range(8):
            self.ledger.record(buy(id=str(i), quantity=1, price=1))
        with self.assertRaises(ValidationError):
            self.ledger.record(factual(id='ninth', quantity=1, price=1))

    def test_exception_records_are_replayable(self):
        expected = self.ledger.record(factual(quantity=6000, price=10000, allowance_checked=False))
        restored = Ledger.restore(Path(self.tmp.name) / 'restored.sqlite3', self.ledger.export())
        self.assertEqual(restored.get(), expected)
        self.assertEqual(len(summary(restored.get())['recording_warnings']), 1)
        self.assertEqual(Ledger(restored.path, 'journal').get(), expected)

    def test_restart_detects_state_corruption_without_rewriting_it(self):
        self.ledger.record(buy())
        changed = self.ledger.get()
        changed['free_cash'] -= 123
        with self.ledger.connect() as db:
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(changed),))
        with self.assertRaises(ValidationError):
            Ledger(self.path, 'journal')
        self.assertEqual(self.ledger.get(), changed)

    def test_restart_detects_event_digest_corruption(self):
        self.ledger.record(buy())
        with self.ledger.connect() as db:
            db.execute('UPDATE events SET digest=?', ('0' * 64,))
        with self.assertRaises(ValidationError):
            Ledger(self.path, 'journal')

    def test_item_quantity_conservation_not_just_money(self):
        self.ledger.record(buy())
        self.ledger.record(dict(id='fill', type='fill', offer_id='buy', quantity=10, amount=1000))
        state = self.ledger.record(dict(id='collect', type='collect', offer_id='buy'))
        state['lots'][0]['quantity'] += 1  # Cash and cost totals are unchanged.
        with self.assertRaises(ValidationError):
            check_invariants(state)

    def test_v01_backup_schema_can_be_restored(self):
        self.ledger.record(buy())
        backup = self.ledger.export()
        backup['schema'] = 'osrs-flip-lab-journal-v0.1'
        restored = Ledger.restore(Path(self.tmp.name) / 'legacy.sqlite3', backup)
        self.assertEqual(restored.get(), self.ledger.get())

    def test_bad_backup_sequence_rejected_before_creation(self):
        self.ledger.record(buy())
        self.ledger.record(dict(id='cancel', type='cancel', offer_id='buy'))
        backup = self.ledger.export()
        backup['events'][1]['seq'] = backup['events'][0]['seq']
        dest = Path(self.tmp.name) / 'bad.sqlite3'
        with self.assertRaises(ValidationError):
            Ledger.restore(dest, backup)
        self.assertFalse(dest.exists())

    def test_malformed_backup_is_validation_error(self):
        for b in (None, [], {}, {'schema': 'osrs-flip-lab-journal-v0.2', 'mode': 'journal'}):
            with self.subTest(backup=b), self.assertRaises(ValidationError):
                Ledger.restore(Path(self.tmp.name) / 'bad.sqlite3', b)
        self.assertFalse((Path(self.tmp.name) / 'bad.sqlite3').exists())

    def test_restart_malformed_json_has_clear_validation_error(self):
        with self.ledger.connect() as db:
            db.execute('UPDATE state SET body=? WHERE id=1', ('{broken',))
        with self.assertRaises(ValidationError):
            Ledger(self.path, 'journal')


if __name__ == '__main__':
    unittest.main()
