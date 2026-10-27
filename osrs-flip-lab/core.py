"""Independent, local-only prototype core. No vendor, game, or network calls.

All money and quantity values are integers. Fill entries are DELTAS, not cumulative
amounts. Sell proceeds must be ACTUAL NET cash: tax is not deducted a second time.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

MAX_INTEGER = 9_000_000_000_000_000
BACKUP_SCHEMA = 'osrs-flip-lab-journal-v0.3'
SUPPORTED_BACKUP_SCHEMAS = {'osrs-flip-lab-journal-v0.1', 'osrs-flip-lab-journal-v0.2', BACKUP_SCHEMA}


class ValidationError(ValueError):
    pass


def integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValidationError(f'{name} must be a whole number from {minimum:,} to {MAX_INTEGER:,}.')
    return value


def tax_per_unit(sell_price: int, exempt: bool = False) -> int:
    """Stipulated ordinary 2% regime, not a verified exhaustive item-rule registry."""
    integer(sell_price, 'Sale price')
    if type(exempt) is not bool:
        raise ValidationError('Tax-exemption status must be an explicit boolean, not text or a number.')
    return 0 if exempt else min(sell_price // 50, 5_000_000)


def new_state(cash: int = 500_000_000, exposure_cap: int = 50_000_000) -> dict:
    integer(cash, 'Starting cash', 1)
    integer(exposure_cap, 'Exposure ceiling', 1)
    return dict(initial_cash=cash, free_cash=cash, exposure_cap=exposure_cap,
                slot_count=8, realized_profit=0, offers={}, lots=[])


def summary(state: dict) -> dict:
    escrow = sum(o['escrow'] for o in state['offers'].values())
    claimable = sum(o['claimable'] for o in state['offers'].values())
    inventory_cost = sum(lot['cost'] for lot in state['lots'])
    pending_cost = sum(o['pending_cost'] + o['reserved_cost'] for o in state['offers'].values())
    by_item: dict[int, dict] = {}
    for lot in state['lots']:
        item = by_item.setdefault(lot['item_id'], dict(item_id=lot['item_id'], name=lot['name'], quantity=0, cost=0))
        item['quantity'] += lot['quantity']
        item['cost'] += lot['cost']
    active = sum(o['status'] != 'CLOSED' for o in state['offers'].values())
    recording_warnings = [dict(offer_id=o['id'], flags=o['recording_warnings'],
                               reason=o['exception_reason'])
                          for o in state['offers'].values() if o.get('recording_warnings')]
    return dict(recording_warnings=recording_warnings,
                exposure_over_cap=(escrow + inventory_cost + pending_cost > state['exposure_cap']),
                free_cash=state['free_cash'], escrow=escrow, claimable_net_cash=claimable,
                inventory_cost=inventory_cost + pending_cost,
                committed_at_cost=escrow + inventory_cost + pending_cost,
                realized_profit=state['realized_profit'], active_slots=active,
                available_slots=state['slot_count'] - active,
                accounting_total_at_cost=state['free_cash'] + escrow + claimable + inventory_cost + pending_cost,
                inventory=list(by_item.values()))


def check_invariants(state: dict) -> None:
    totals = summary(state)
    if totals['accounting_total_at_cost'] != state['initial_cash'] + state['realized_profit']:
        raise ValidationError('Cash / inventory cost reconciliation failed.')
    integer(state['free_cash'], 'Free cash')
    if totals['active_slots'] > state['slot_count']:
        raise ValidationError('No free offer slot: collect a terminal offer first.')
    held = {}
    net_bought = {}
    for o in state['offers'].values():
        item_id = o['item_id']
        net_bought[item_id] = net_bought.get(item_id, 0) + (o['filled'] if o['side'] == 'BUY' else -o['filled'])
        held[item_id] = held.get(item_id, 0) + o['pending_qty'] + o['reserved_qty']
        for key in ['filled', 'escrow', 'claimable', 'pending_qty', 'pending_cost', 'reserved_qty', 'reserved_cost']:
            integer(o[key], key)
        if o['filled'] > o['quantity']:
            raise ValidationError('Fill quantity exceeds order quantity.')
    for lot in state['lots']:
        integer(lot['quantity'], 'Inventory quantity', 1)
        integer(lot['cost'], 'Inventory cost')
        held[lot['item_id']] = held.get(lot['item_id'], 0) + lot['quantity']
    if any(held.get(item, 0) != net_bought.get(item, 0) for item in held.keys() | net_bought.keys()):
        raise ValidationError('Item-quantity reconciliation failed.')


def take_inventory(state: dict, item_id: int, quantity: int) -> int:
    if sum(lot['quantity'] for lot in state['lots'] if lot['item_id'] == item_id) < quantity:
        raise ValidationError('Not enough collected inventory. Collect bought items first.')
    remaining, cost = quantity, 0
    for lot in state['lots']:
        if lot['item_id'] != item_id or remaining == 0:
            continue
        count = min(remaining, lot['quantity'])
        allocated = lot['cost'] * count // lot['quantity']
        lot['quantity'] -= count
        lot['cost'] -= allocated
        remaining -= count
        cost += allocated
    state['lots'] = [lot for lot in state['lots'] if lot['quantity']]
    return cost


def add_inventory(state: dict, offer: dict, quantity: int, cost: int) -> None:
    if quantity:
        state['lots'].append(dict(item_id=offer['item_id'], name=offer['name'], quantity=quantity, cost=cost))


def validate_event_identity(event: Any) -> None:
    """Reject malformed input before JSON serialization or SQLite parameter binding."""
    if not isinstance(event, dict):
        raise ValidationError('An event must be a JSON object.')
    eid = event.get('id')
    if not isinstance(eid, str) or not 1 <= len(eid) <= 80:
        raise ValidationError('A stable event ID of 1–80 characters is required.')
    link = event.get('decision_id')
    if link is not None and (not isinstance(link,str) or not 1 <= len(link) <= 80):
        raise ValidationError('Decision link must be a saved receipt ID of 1–80 characters.')
    kind = event.get('type')
    if kind not in ('place', 'fill', 'cancel', 'collect'):
        raise ValidationError('Unsupported event type.')
    if kind != 'place':
        oid = event.get('offer_id')
        if not isinstance(oid, str) or not 1 <= len(oid) <= 80:
            raise ValidationError('A valid offer ID is required.')
    if 'record_external_action' in event and type(event['record_external_action']) is not bool:
        raise ValidationError('Already-executed acknowledgement must be an explicit boolean.')


def apply_event(previous: dict, event: dict) -> dict:
    """Pure state reducer. An unsuccessful event cannot partially mutate state."""
    validate_event_identity(event)
    state = deepcopy(previous)
    kind = event['type']
    eid = event['id']
    if kind == 'place':
        if eid in state['offers']:
            raise ValidationError('Duplicate offer ID.')
        side = event.get('side')
        if side not in ('BUY', 'SELL'):
            raise ValidationError('Choose BUY or SELL.')
        item_id = integer(event.get('item_id'), 'Item ID', 1)
        name = event.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValidationError('An item name of 1–120 characters is required.')
        quantity = integer(event.get('quantity'), 'Quantity', 1)
        price = integer(event.get('price'), 'Offered unit price', 1)
        total = integer(quantity * price, 'Order commitment', 1)
        if summary(state)['available_slots'] < 1:
            raise ValidationError('No free offer slot.')
        offer = dict(id=eid, side=side, item_id=item_id, name=name.strip(), quantity=quantity,
                     price=price, filled=0, escrow=0, claimable=0, pending_qty=0, pending_cost=0,
                     reserved_qty=0, reserved_cost=0, status='OPEN')
        if side == 'BUY':
            warnings = []
            if event.get('allowance_checked') is not True:
                warnings.append('ALLOWANCE_NOT_VERIFIED')
            if total + summary(state)['committed_at_cost'] > state['exposure_cap']:
                warnings.append('EXPOSURE_CEILING_EXCEEDED')
            # Advice must obey limits. A journal must also preserve genuine actions
            # that already happened, without disguising them as approved trades.
            if warnings and event.get('record_external_action') is not True:
                raise ValidationError('Policy check failed: ' + ', '.join(warnings) +
                    '. Do not proceed on this basis. For an action ALREADY performed, use the explicit factual-record exception with a reason.')
            if warnings:
                reason = event.get('exception_reason')
                if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500:
                    raise ValidationError('An already-executed exception requires a factual reason of 10–500 characters.')
                offer['recording_warnings'] = warnings
                offer['exception_reason'] = reason.strip()
            if total > state['free_cash']:
                raise ValidationError('Insufficient recorded cash. Reconcile opening state; an exception cannot create money.')
            state['free_cash'] -= total
            offer['escrow'] = total
        else:
            offer['reserved_cost'] = take_inventory(state, item_id, quantity)
            offer['reserved_qty'] = quantity
        if event.get('decision_id') is not None:
            offer['decision_id'] = event['decision_id']
        state['offers'][eid] = offer
    elif kind in ('fill', 'cancel', 'collect'):
        offer = state['offers'].get(event.get('offer_id'))
        if offer is None or offer['status'] == 'CLOSED':
            raise ValidationError('Offer does not exist or is already closed.')
        if kind == 'fill':
            if offer['status'] != 'OPEN':
                raise ValidationError('Cannot fill a terminal offer.')
            quantity = integer(event.get('quantity'), 'Newly filled quantity', 1)
            amount = integer(event.get('amount'), 'Actual cash delta', 1)
            if quantity + offer['filled'] > offer['quantity']:
                raise ValidationError('Fill exceeds the remaining quantity. Enter a delta, not the cumulative total.')
            if offer['side'] == 'BUY':
                if amount < quantity:
                    raise ValidationError('Total purchase cost cannot be below 1 GP per purchased unit. Check quantity and cash units.')
                if amount > quantity * offer['price']:
                    raise ValidationError('Paid amount exceeds the recorded limit. Correct the source record first.')
                offer['escrow'] -= amount
                offer['pending_qty'] += quantity
                offer['pending_cost'] += amount
            else:
                if event.get('amount_basis') != 'ACTUAL_NET':
                    raise ValidationError('Sale proceeds must be actual NET cash, after any tax.')
                cost = offer['reserved_cost'] * quantity // offer['reserved_qty']
                offer['reserved_cost'] -= cost
                offer['reserved_qty'] -= quantity
                offer['claimable'] += amount
                state['realized_profit'] += amount - cost
            offer['filled'] += quantity
            if offer['filled'] == offer['quantity']:
                offer['status'] = 'COMPLETE'
        elif kind == 'cancel':
            if offer['status'] != 'OPEN':
                raise ValidationError('Only an open offer can be cancelled.')
            offer['status'] = 'CANCELLED'
        else:
            changed = False
            if offer['side'] == 'BUY':
                changed = offer['pending_qty'] > 0
                add_inventory(state, offer, offer['pending_qty'], offer['pending_cost'])
                offer['pending_qty'] = offer['pending_cost'] = 0
                required = (offer['quantity'] - offer['filled']) * offer['price'] if offer['status'] == 'OPEN' else 0
                refund = offer['escrow'] - required
                state['free_cash'] += refund
                offer['escrow'] -= refund
                changed = changed or refund > 0
            else:
                changed = offer['claimable'] > 0
                state['free_cash'] += offer['claimable']
                offer['claimable'] = 0
                if offer['status'] == 'CANCELLED':
                    changed = changed or offer['reserved_qty'] > 0
                    add_inventory(state, offer, offer['reserved_qty'], offer['reserved_cost'])
                    offer['reserved_qty'] = offer['reserved_cost'] = 0
            if offer['status'] in ('CANCELLED', 'COMPLETE'):
                offer['status'] = 'CLOSED'
                changed = True
            if not changed:
                raise ValidationError('Nothing is available to collect.')
    else:
        raise ValidationError('Unsupported event type.')
    check_invariants(state)
    return state


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    """Persistent append-only events with an atomically updated replayable state."""
    def __init__(self, path: Path, mode: str, starting_cash: int = 500_000_000):
        if mode not in ('demo', 'journal'):
            raise ValidationError('Mode must be demo or journal.')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT, initial TEXT, body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, digest TEXT, observed_at TEXT, body TEXT)')
            initial = json.dumps(new_state(starting_cash), sort_keys=True)
            db.execute('INSERT OR IGNORE INTO state VALUES(1,?,?,?)', (mode, initial, initial))
            existing = db.execute('SELECT mode FROM state WHERE id=1').fetchone()[0]
            if existing != mode:
                raise ValidationError('Demo and real journal databases must remain separate.')
        self.mode = mode
        self.verify_integrity()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self) -> dict:
        with self.connect() as db:
            return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])

    def record(self, event: dict) -> dict:
        validate_event_identity(event)
        body = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT digest FROM events WHERE id=?', (event.get('id'),)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ValidationError('Event ID was already used for different data.')
                return json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            updated = apply_event(state, event)
            db.execute('INSERT INTO events(id,digest,observed_at,body) VALUES(?,?,?,?)',
                       (event['id'], digest, utc_now(), body))
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(updated, sort_keys=True),))
            return updated

    def export(self) -> dict:
        with self.connect() as db:
            db.execute('BEGIN')
            state = db.execute('SELECT * FROM state WHERE id=1').fetchone()
            events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY seq')]
        return dict(schema=BACKUP_SCHEMA, mode=state['mode'],
                    initial=json.loads(state['initial']), state=json.loads(state['body']), events=events,
                    note='Observation times are recording times, not guaranteed server execution times.')

    def verify_integrity(self) -> dict:
        """Replay-check the stored journal before trusting its saved current state.

        This detects corruption/inconsistency, not deliberate coordinated editing.
        The snapshot is read in one SQLite transaction by export().
        """
        try:
            return validate_backup(self.export())
        except ValidationError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError('Saved journal contains malformed data. Preserve it for review; no automatic repair was performed.') from exc

    @classmethod
    def restore(cls, path: Path, backup: dict) -> 'Ledger':
        path = Path(path)
        if path.exists():
            raise ValidationError('Restore destination must not exist. Preserve the original database.')
        replay = validate_backup(backup)
        ledger = cls(path, backup['mode'], backup['initial']['initial_cash'])
        with ledger.connect() as db:
            for row in backup['events']:
                db.execute('INSERT INTO events(id,digest,observed_at,body) VALUES(?,?,?,?)',
                           (row['id'], row['digest'], row['observed_at'], row['body']))
            db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(replay, sort_keys=True),))
        ledger.verify_integrity()
        return ledger


def validate_backup(backup: dict) -> dict:
    """Validate legacy/current backup contents before writing or trusting state."""
    try:
        if not isinstance(backup, dict) or backup.get('schema') not in SUPPORTED_BACKUP_SCHEMAS or backup.get('mode') not in ('demo', 'journal'):
            raise ValidationError('Unsupported backup schema or mode.')
        initial = backup['initial']
        if initial != new_state(initial['initial_cash']):
            raise ValidationError('Unsupported initial state.')
        if not isinstance(backup['events'], list):
            raise ValidationError('Backup events must be an ordered list.')
        replay = deepcopy(initial)
        seen = set()
        previous_seq = 0
        for row in backup['events']:
            seq = integer(row['seq'], 'Event sequence', 1)
            if seq <= previous_seq:
                raise ValidationError('Backup event sequence is not strictly increasing.')
            previous_seq = seq
            event = json.loads(row['body'])
            validate_event_identity(event)
            canonical = json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if row['id'] in seen or event['id'] != row['id'] or hashlib.sha256(canonical.encode()).hexdigest() != row['digest']:
                raise ValidationError('Backup event integrity check failed.')
            if not isinstance(row['observed_at'], str):
                raise ValidationError('Missing event observation time.')
            seen.add(row['id'])
            replay = apply_event(replay, event)
        check_invariants(replay)
        if replay != backup['state']:
            raise ValidationError('Saved state does not reconcile to replayed events. Preserve the original database and review it; no automatic repair was performed.')
        return replay
    except ValidationError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValidationError('Malformed journal or backup structure.') from exc


def scan_fixture(fixture: dict, state: dict) -> list[dict]:
    """Synthetic-data demonstration only. No calibrated capacity or fill forecasts."""
    now = fixture['as_of']
    totals = summary(state)
    budget = min(state['free_cash'], max(0, state['exposure_cap'] - totals['committed_at_cost']))
    candidates = []
    for raw in fixture['items']:
        row = dict(raw)
        low, high = raw['low'], raw['high']
        if low <= 0 or high <= low or min(raw['low_units'], raw['high_units']) <= 0:
            continue
        if any(not 0 <= now - raw[key] <= 300 for key in ('low_time', 'high_time')):
            continue
        buy, sell = low + 1, high - 1
        profit = sell - tax_per_unit(sell, raw['tax_exempt']) - buy
        if profit <= 0:
            continue
        # 1% of the smaller synthetic 5m unit count is an ASSUMPTION, not a capacity estimate.
        qty = min(raw['illustrative_limit'], min(raw['low_units'], raw['high_units']) // 100,
                  5_000_000 // buy)
        if qty:
            row.update(buy=buy, sell=sell, unit_profit=profit, quantity=qty,
                       conditional_profit=profit * qty, cost=buy * qty)
            candidates.append(row)
    candidates.sort(key=lambda row: (-row['conditional_profit'], row['item_id']))
    selected = []
    slots = totals['available_slots']
    for row in candidates:
        qty = min(row['quantity'], budget // row['buy'])
        if not qty or not slots:
            continue
        row.update(quantity=qty, cost=qty * row['buy'], conditional_profit=qty * row['unit_profit'])
        budget -= row['cost']
        slots -= 1
        selected.append(row)
    return selected
