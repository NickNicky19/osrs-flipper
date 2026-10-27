"""Public, simulated research pipeline. No private journal or game access.

The data branch contains only this module's explicitly validated state schema.
Each run has one writer, atomic state replacement, and a stable run ID. A failed
source never turns an old quote into fresh advice. Publication is acknowledged
only after the Pages deployment action succeeds.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from core import ValidationError, tax_per_unit
from market import SourceContract, parse_payload, strict_json, MAX_RAW_BYTES
from research import break_even_price
import learning

ROOT = Path(__file__).resolve().parent
SCHEMA = 'osrs-public-paper-1'
POLICY = 'rules-baseline-1'
HOST = 'prices.runescape.wiki'
BANKROLL = 500_000_000
LIVE_PILOT_CEILING = 50_000_000
MAX_POSITION = 125_000_000
MAX_SLOTS = 4
MIN_CONDITIONAL_NET = 50_000
PICK_TTL = 600
OBS_RETENTION = 10 * 86400
PICK_RETENTION = 30 * 86400
MAX_STATE_BYTES = 12_000_000
MAX_FETCHES = 18
MAX_RETRIES = 2
MAX_RAW_TOTAL = 18_000_000
MAX_SAFE_INTEGER = 2**53 - 1
VERSION = 'public-pipeline-1'
OPTIONAL_STATE_KEYS = {'hourly', 'hourly_at', 'last_24h_at', 'daily_volume', 'data_gap',
                       'history_errors', 'learning_reason', 'latest_screen',
                       'latest_screen_at', 'latest_capture_hashes', 'chart_budget_day',
                       'chart_budget_used', 'recent_revision_bucket', 'last_revision_scan_at'}
PRIVATE_KEY = re.compile(r'(account|journal|credential|password|secret|token|api.?key|'
                         r'auth|cookie|rsn|email|local.?path|user.?name)', re.I)


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def new_state(now=None):
    now = int(time.time()) if now is None else now
    return dict(schema=SCHEMA, created_at=now, policy=POLICY, bankroll=BANKROLL,
                paper_cash=BANKROLL, paper_reserved=0, paper_inventory=[],
                observations=[], picks=[], outcomes=[], capture_metadata=[],
                archived_counts=dict(picks=0, outcomes=0, losses=0),
                mapping=None, mapping_at=0, last_5m_bucket=None,
                history_cache={},
                chart_budget_day=now//86400, chart_budget_used=0,
                last_source_success_at=None, last_run_at=None, run_ids=[],
                source_status='DATA_UNAVAILABLE', source_error='NO_CAPTURE_YET',
                last_raw_evidence_day=None,
                circuit_open_until=0, kill_switch=False, public_pause=False,
                models=[], predictions=[], last_learning_check=0,
                shadow_decisions=[],
                learning_status='DATA_INSUFFICIENT', active_model_id=None,
                last_training_at=None, last_evaluation_at=None, latest_data_cutoff=None,
                shadow_metrics={}, promotion_gate=None, portfolio_shadow_comparison=None,
                publication_receipts=[], active_pick_ids=[])


def validate_state(state):
    if not isinstance(state, dict) or state.get('schema') != SCHEMA:
        raise ValidationError('Missing or unknown durable public state; refusing to reset the paper account.')
    if state.get('bankroll') != BANKROLL or state.get('policy') != POLICY:
        raise ValidationError('Unexpected bankroll or policy change.')
    extra = set(state) - set(new_state(0)) - OPTIONAL_STATE_KEYS
    if extra:
        raise ValidationError('Unknown durable state fields: ' + ','.join(sorted(extra)))
    def public_only(value, depth=0):
        if depth > 16:
            raise ValidationError('State nesting limit exceeded.')
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or PRIVATE_KEY.search(key):
                    raise ValidationError('Private or invalid state field.')
                public_only(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                public_only(child, depth+1)
        elif isinstance(value, str):
            if re.search(r'(?i)(?:[A-Z]:\\|/home/|/Users/|sk-proj-|ghp_|PRIVATE_ACCOUNT_SECRET_CANARY)', value):
                raise ValidationError('Private-looking value in public state.')
        elif value is not None and type(value) not in (int, float, bool):
            raise ValidationError('Invalid public state value type.')
        if type(value) is float and not math.isfinite(value):
            raise ValidationError('Non-finite public state value.')
    public_only(state)
    if not isinstance(state.get('paper_cash'), int) or state['paper_cash'] < 0:
        raise ValidationError('Invalid simulated cash.')
    if not isinstance(state.get('history_cache'), dict):
        raise ValidationError('Invalid history cache.')
    if state.get('paper_reserved', 0) < 0 or state['paper_cash'] + state['paper_reserved'] != BANKROLL:
        raise ValidationError('Paper cash conservation failed.')
    active = set(state.get('active_pick_ids', []))
    if len(active) != len(state.get('active_pick_ids', [])):
        raise ValidationError('Duplicate active paper plan.')
    if sum(p.get('required_cash', 0) for p in state.get('picks', []) if p.get('id') in active) != state['paper_reserved']:
        raise ValidationError('Paper reservation ledger mismatch.')
    for key in ('observations', 'picks', 'outcomes', 'capture_metadata', 'models', 'predictions', 'shadow_decisions', 'run_ids'):
        if not isinstance(state.get(key), list):
            raise ValidationError('Invalid public state collection: ' + key)
    return state


def load_state(path):
    if not Path(path).is_file():
        raise ValidationError('Durable state missing. Restore the data branch; do not invent a new paper portfolio.')
    raw = Path(path).read_bytes()
    if len(raw) > MAX_STATE_BYTES:
        raise ValidationError('Durable state exceeds limit; pipeline paused.')
    return validate_state(strict_json(raw))


def save_state(path, state):
    validate_state(state)
    # A full screener is a current-run site artifact, not durable learning or
    # account state. Omitting it avoids rewriting ~2 MB on every data commit.
    persisted = {k: v for k, v in state.items() if k != 'latest_screen'}
    raw = json.dumps(persisted, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_STATE_BYTES:
        raise ValidationError('Durable state exceeds 12 MB; preserve and investigate before another issue.')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    return hashlib.sha256(raw).hexdigest()


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValidationError('Unexpected source redirect.')


def _retry_delay(value, attempt):
    if value and str(value).isdigit():
        return min(30, int(value))
    return min(8, 2 ** attempt) + random.Random(attempt).random()


class SourceClient:
    def __init__(self, contract, raw_dir=None, opener=None, sleeper=time.sleep):
        self.contract = contract
        self.opener = opener or urllib.request.build_opener(NoRedirects())
        self.sleeper = sleeper
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self.calls = 0
        self.raw_bytes = 0

    def fetch(self, kind, params=None, now=None):
        if kind not in ('mapping', 'latest', '5m', '1h', '24h', 'timeseries'):
            raise ValidationError('Unsupported source route.')
        if self.calls >= MAX_FETCHES:
            raise ValidationError('Source request budget exhausted.')
        version = self.contract.version_for(kind)
        query = urllib.parse.urlencode(params or {})
        url = f'https://{HOST}/api/{version}/osrs/{kind}' + ('?' + query if query else '')
        requested_at = int(time.time()) if now is None else now
        for attempt in range(MAX_RETRIES + 1):
            if self.calls >= MAX_FETCHES:
                raise ValidationError('Source request budget exhausted.')
            self.calls += 1
            request = urllib.request.Request(url, headers={'User-Agent': self.contract.user_agent,
                'Accept': 'application/json', 'Accept-Encoding': 'identity'})
            try:
                with self.opener.open(request, timeout=15) as response:
                    if response.status != 200 or urllib.parse.urlsplit(response.url).hostname != HOST:
                        raise ValidationError('Unexpected source response or host.')
                    if response.headers.get_content_type() not in ('application/json', 'text/json'):
                        raise ValidationError('Unexpected source content type.')
                    raw = response.read(MAX_RAW_BYTES + 1)
                    if len(raw) > MAX_RAW_BYTES or self.raw_bytes + len(raw) > MAX_RAW_TOTAL:
                        raise ValidationError('Raw source budget exhausted.')
                    self.raw_bytes += len(raw)
                    received_at = int(time.time()) if now is None else now
                    sha = hashlib.sha256(raw).hexdigest()
                    metadata = dict(kind=kind, version=version, url=url, requested_at=requested_at,
                        received_at=received_at, sha256=sha, bytes=len(raw),
                        server_date=response.headers.get('Date'), age=response.headers.get('Age'),
                        cache_control=response.headers.get('Cache-Control'), status=200)
                    if self.raw_dir:
                        self.raw_dir.mkdir(parents=True, exist_ok=True)
                        (self.raw_dir / (sha + '.json')).write_bytes(raw)
                    return raw, metadata
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                    raise ValidationError(f'HTTP_{exc.code}') from exc
                self.sleeper(_retry_delay(exc.headers.get('Retry-After'), attempt))
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt == MAX_RETRIES:
                    raise ValidationError('TRANSIENT_NETWORK_FAILURE: ' + type(exc).__name__) from exc
                self.sleeper(_retry_delay(None, attempt))
        raise AssertionError('Unreachable retry loop')


def parse_timeseries(raw, received_at, item_id):
    root = strict_json(raw)
    if not isinstance(root, dict) or root.get('itemId') != item_id:
        raise ValidationError('Timeseries item identity mismatch.')
    step = root.get('timestep')
    start, end = root.get('startTimestamp'), root.get('endTimestamp')
    if type(step) is not int or step < 300 or type(start) is not int or type(end) is not int or start >= end:
        raise ValidationError('Invalid declared timeseries window.')
    if end > received_at + step:
        raise ValidationError('Future timeseries window.')
    data = root.get('data')
    if not isinstance(data, list) or len(data) > 10_000:
        raise ValidationError('Invalid timeseries data.')
    points = []
    seen = set()
    for row in data:
        if not isinstance(row, dict) or type(row.get('timestamp')) is not int:
            raise ValidationError('Invalid timeseries point.')
        ts = row['timestamp']
        if ts in seen or not start <= ts < end or ts % step:
            raise ValidationError('Duplicate or misaligned timeseries point.')
        seen.add(ts)
        if ts + step > received_at:
            continue
        item = {}
        for side in ('Low', 'High'):
            price, units = row.get('avg' + side + 'Price'), row.get(side.lower() + 'PriceVolume')
            if price is not None and (type(price) not in (int, float) or not math.isfinite(price) or price <= 0 or price > 10**12):
                raise ValidationError('Invalid timeseries average.')
            if units is not None and (type(units) is not int or units < 0 or units > 10**12):
                raise ValidationError('Invalid timeseries units.')
            item[side.lower() + '_avg'] = price
            item[side.lower() + '_units'] = units
        points.append(dict(bucket_start=ts, **item))
    return dict(item_id=item_id, start=start, end=end, timestep=step, points=sorted(points, key=lambda x: x['bucket_start']),
                availability='RETROSPECTIVE_DOWNLOAD', exact_price_volume=None, attainable_quantity=None)


def _parse_safe(kind, raw, at, contract):
    """Quarantine bad items while keeping a valid bulk envelope and good items."""
    root = strict_json(raw)
    if kind == 'mapping':
        if not isinstance(root, list) or not root:
            raise ValidationError('Invalid mapping envelope.')
        items, rejected = {}, {}
        for entry in root:
            try:
                parsed = parse_payload(kind, json.dumps([entry]).encode(), at, contract)
                ident, value = next(iter(parsed['items'].items()))
                if ident in items:
                    raise ValidationError('Duplicate mapping item.')
                items[ident] = value
            except (ValidationError, TypeError, KeyError) as exc:
                rejected[str(entry.get('id', '?')) if isinstance(entry, dict) else '?'] = str(exc)
        return dict(items=items, rejected=rejected)
    if not isinstance(root, dict) or not isinstance(root.get('data'), dict):
        raise ValidationError('Invalid market envelope.')
    items, rejected = {}, {}
    for ident, entry in root['data'].items():
        try:
            envelope = dict(data={ident: entry})
            if kind in ('5m', '1h', '24h'):
                envelope['timestamp'] = root.get('timestamp')
            parsed = parse_payload(kind, json.dumps(envelope).encode(), at, contract)
            items.update(parsed['items'])
        except (ValidationError, TypeError, KeyError) as exc:
            rejected[str(ident)] = str(exc)
    result = dict(items=items, rejected=rejected)
    if kind in ('5m', '1h', '24h'):
        # Validate the envelope even when every item happens to be invalid.
        parsed = parse_payload(kind, json.dumps(dict(data={}, timestamp=root.get('timestamp'))).encode(), at, contract)
        result.update(start=parsed['start'], end=parsed['end'], seconds=parsed['seconds'])
    return result


def collect(state, client, now):
    if state.get('circuit_open_until', 0) > now:
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'SOURCE_CIRCUIT_OPEN'
        return dict(ok=False, error='SOURCE_CIRCUIT_OPEN', captures=[])
    if client.contract.blockers(now):
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'SOURCE_CONTRACT_NOT_REVIEWED'
        return dict(ok=False, error='SOURCE_CONTRACT_NOT_REVIEWED', captures=[])
    fetched, captures = {}, []
    routes = ['latest', '5m']
    if state.get('hourly') is None or now - state.get('hourly_at', 0) > 3600:
        routes.append('1h')
    if state['mapping'] is None or now - state['mapping_at'] > 86400:
        routes.insert(0, 'mapping')
    if now // 86400 != (state.get('last_24h_at') or 0) // 86400:
        routes.append('24h')
    try:
        for kind in routes:
            raw, meta = client.fetch(kind, now=now)
            parsed = _parse_safe(kind, raw, meta['received_at'], client.contract)
            if not parsed['items']:
                raise ValidationError(kind.upper() + '_NO_USABLE_ITEMS')
            fetched[kind] = parsed
            meta['accepted_items'] = len(parsed['items'])
            meta['quarantined_items'] = len(parsed['rejected'])
            captures.append(meta)
        if '1h' not in fetched:
            fetched['1h'] = state['hourly']
        # The bulk latest 5m response is a publication watermark. Backfill only
        # documented timestamp requests and at most twelve missing buckets.
        current = fetched['5m']['start']
        previous = state.get('last_5m_bucket')
        if previous is not None and current > previous + 300:
            missing = list(range(previous + 300, current, 300))
            if len(missing) > 12:
                state['data_gap'] = dict(from_bucket=missing[0], to_bucket=missing[-1], reason='BACKFILL_BOUND')
                missing = missing[-12:]
            for bucket in missing:
                raw, meta = client.fetch('5m', {'timestamp': bucket}, now=now)
                parsed = _parse_safe('5m', raw, meta['received_at'], client.contract)
                if parsed['start'] != bucket:
                    raise ValidationError('Backfill timestamp mismatch.')
                fetched['5m:' + str(bucket)] = parsed
                captures.append(meta)
        if previous is not None and current >= previous and now - state.get('last_revision_scan_at', 0) >= 3600:
            bucket = current - 300
            if bucket >= 0 and '5m:' + str(bucket) not in fetched:
                raw, meta = client.fetch('5m', {'timestamp': bucket}, now=now)
                parsed = _parse_safe('5m', raw, meta['received_at'], client.contract)
                if parsed['start'] != bucket:
                    raise ValidationError('Revision timestamp mismatch.')
                fetched['5m:' + str(bucket)] = parsed
                captures.append(meta)
            state['last_revision_scan_at'] = now
        state['source_status'] = 'CAPTURED_WITH_REVIEW'
        state['source_error'] = None
        state['last_source_success_at'] = now
        state['last_5m_bucket'] = current
        if 'mapping' in fetched:
            state['mapping'] = fetched['mapping']['items']
            state['mapping_at'] = now
        if '1h' in routes:
            state['hourly'] = fetched['1h']
            state['hourly_at'] = now
        if '24h' in fetched:
            state['last_24h_at'] = now
            state['daily_volume'] = {key: [value.get('low_units'), value.get('high_units')]
                                     for key, value in fetched['24h']['items'].items()}
        state['circuit_open_until'] = 0
        state['capture_metadata'].extend(captures)
        state['capture_metadata'] = [c for c in state['capture_metadata'] if c['received_at'] >= now - OBS_RETENTION][-2000:]
        return dict(ok=True, data=fetched, captures=captures)
    except ValidationError as exc:
        message = str(exc)
        state['source_status'] = 'DATA_GAP' if message.startswith('TRANSIENT_') or message.startswith('HTTP_5') or message == 'HTTP_429' else 'PAUSED'
        state['source_error'] = message
        if message.startswith('HTTP_403') or message.startswith('HTTP_401') or state['source_status'] == 'PAUSED':
            state['circuit_open_until'] = now + 86400
        elif message == 'HTTP_429':
            state['circuit_open_until'] = now + 1800
        elif state['source_status'] == 'DATA_GAP':
            state['circuit_open_until'] = now + 300
        state['capture_metadata'].extend(captures)
        state['capture_metadata'] = state['capture_metadata'][-2000:]
        return dict(ok=False, error=message, captures=captures)


def _tax_status(name):
    # The named exclusions are from the GE exemption list. Any uncertain variant
    # is excluded; the ordinary rule is applied only to reviewed IDs in config.
    return 'UNREVIEWED'


def load_rules(path):
    rules = strict_json(Path(path).read_bytes())
    if not isinstance(rules, dict) or rules.get('version') != 'public-rules-1':
        raise ValidationError('Invalid public item rules.')
    if not isinstance(rules.get('ordinary_ids'), list) or not isinstance(rules.get('exempt_ids'), list):
        raise ValidationError('Invalid public rule lists.')
    if set(rules['ordinary_ids']) & set(rules['exempt_ids']):
        raise ValidationError('Conflicting tax rules.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rules.get('valid_until', '')):
        raise ValidationError('Public tax-rule expiry required.')
    return rules


def _rule(item_id, rules):
    if item_id in rules['ordinary_ids']:
        return False
    if item_id in rules['exempt_ids']:
        return True
    return None


def graph_stats(points):
    if not points:
        return dict(coverage=0, lowest_low_average=None, low_bucket_units=None,
                    exact_units_at_price=None, attainable_quantity=None, note='No numerical history available.')
    valid = [p for p in points if p.get('low_avg') is not None and p.get('high_avg') is not None]
    if not valid:
        return dict(coverage=0, lowest_low_average=None, low_bucket_units=None,
                    exact_units_at_price=None, attainable_quantity=None)
    lows = sorted(p['low_avg'] for p in valid)
    highs = sorted(p['high_avg'] for p in valid)
    spreads = sorted(p['high_avg']-p['low_avg'] for p in valid)
    low_units = [p['low_units'] for p in valid if type(p.get('low_units')) is int]
    high_units = [p['high_units'] for p in valid if type(p.get('high_units')) is int]
    minimum = min(valid, key=lambda p: p['low_avg'])
    return dict(coverage=len(valid), lowest_low_average=minimum['low_avg'],
                low_bucket_start=minimum['bucket_start'], low_bucket_units=minimum.get('low_units'),
                exact_units_at_price=None, attainable_quantity=None,
                low_p10=lows[int((len(lows) - 1) * .1)], low_median=lows[len(lows)//2],
                high_p10=highs[int((len(highs)-1)*.1)], high_median=highs[len(highs)//2],
                median_spread=spreads[len(spreads)//2],
                historical_low_drawdown_bps=round(10000*(minimum['low_avg']/max(valid,key=lambda p:p['bucket_start'])['low_avg']-1)),
                reported_low_units=sum(low_units) if low_units else None,
                reported_high_units=sum(high_units) if high_units else None,
                first_bucket=min(p['bucket_start'] for p in valid),
                last_bucket=max(p['bucket_start'] for p in valid),
                note='Bucket averages and whole-bucket units; exact-price units and attainable fills are UNKNOWN.')


def screen(mapping, latest, five, hourly, rules, now, daily=None, active_model=None):
    rows = []
    for ident, quote in latest['items'].items():
        item_id = int(ident)
        meta = mapping.get(ident)
        bar = five['items'].get(ident)
        hour = hourly['items'].get(ident)
        flags = []
        low, high = quote.get('low'), quote.get('high')
        if not meta:
            flags.append('NO_MAPPING')
        if low is None or high is None or quote.get('low_time') is None or quote.get('high_time') is None:
            flags.append('MISSING_SIDE')
        elif not (0 <= now - quote['low_time'] <= 300 and 0 <= now - quote['high_time'] <= 300):
            flags.append('STALE_SIDE')
        elif now - quote['low_time'] > 120 or now - quote['high_time'] > 120:
            flags.append('PUBLICATION_WINDOW_TOO_SHORT')
        if now - five['end'] > 900:
            flags.append('STALE_5M_BUCKET')
        if now - hourly['end'] > 7200:
            flags.append('STALE_1H_BUCKET')
        if not bar or not hour or not all(type(bar.get(side + '_units')) is int and bar[side + '_units'] > 0 and
                                           type(hour.get(side + '_units')) is int and hour[side + '_units'] > 0 for side in ('low', 'high')):
            flags.append('TWO_SIDED_ACTIVITY_UNKNOWN')
        limit = meta.get('public_limit') if meta else None
        if limit is None:
            flags.append('PUBLIC_LIMIT_UNKNOWN')
        exempt = _rule(item_id, rules)
        if exempt is None:
            flags.append('TAX_RULE_UNREVIEWED')
        quantity, conditional = 0, None
        if low and high and hour and limit and exempt is not None:
            # 10% of reported hourly units is a named stress sizing assumption,
            # not queue depth, expected fill, or remaining personal allowance.
            quantity = min(limit, hour.get('low_units', 0)//10, hour.get('high_units', 0)//10,
                           MAX_POSITION//low, BANKROLL//low)
            if quantity > 0:
                conditional = (high - tax_per_unit(high, exempt) - low) * quantity
                if abs(conditional) > MAX_SAFE_INTEGER:
                    conditional = None
                    flags.append('PUBLIC_NUMBER_OUT_OF_RANGE')
            if not quantity or conditional is None or conditional <= 0:
                flags.append('NO_POSITIVE_CONDITIONAL_NET')
        if conditional is not None and conditional < MIN_CONDITIONAL_NET:
            flags.append('BELOW_WORTHWHILE_POSITION_FILTER')
        day_units = (daily or {}).get(ident, [None, None])
        row = dict(item_id=item_id, name=meta['name'] if meta else 'Unknown item ' + ident,
                   members=meta.get('members') if meta else None, public_limit=limit,
                   observed_instant_sell_low=low, observed_instant_buy_high=high,
                   low_time=quote.get('low_time'), high_time=quote.get('high_time'),
                   low_age=now-quote['low_time'] if quote.get('low_time') else None,
                   high_age=now-quote['high_time'] if quote.get('high_time') else None,
                   five_low_units=bar.get('low_units') if bar else None,
                   five_high_units=bar.get('high_units') if bar else None,
                   hour_low_units=hour.get('low_units') if hour else None,
                   hour_high_units=hour.get('high_units') if hour else None,
                   day_low_units=day_units[0], day_high_units=day_units[1],
                   quantity_assumption=quantity, required_cash=low*quantity if low else 0,
                   conditional_after_tax_gp=conditional, tax_exempt=exempt,
                   flags=sorted(set(flags)), status='ELIGIBLE_RESEARCH' if not flags else 'EXCLUDED')
        row['ranking_mode'] = 'RULES_BASELINE'
        row['research_rank_score'] = conditional
        if active_model is not None and bar and not flags:
            feature = learning.feature_vector(bar, None)
            if feature is not None:
                forecast = learning.predict(active_model, feature)
                row['research_rank_score'] = conditional + forecast['price_return_bps'] * row['required_cash'] / 10000
                row['ranking_mode'] = 'ML_RESEARCH_ACTIVE'
        rows.append(row)
    rows.sort(key=lambda r: (bool(r['flags']), -(r['research_rank_score'] or -1), r['item_id']))
    return rows


def _select(rows, state, now, existing):
    if state['kill_switch'] or state['public_pause']:
        return []
    available = state['paper_cash']
    selected, used_items = [], set()
    used_items.update(p['item_id'] for p in existing)
    for row in rows:
        if row['flags'] or row['item_id'] in used_items or len(selected) + len(existing) >= MAX_SLOTS:
            continue
        price = row['observed_instant_sell_low']
        qty = min(row['quantity_assumption'], available // price)
        if qty <= 0:
            continue
        conditional = (row['observed_instant_buy_high'] - tax_per_unit(row['observed_instant_buy_high'], row['tax_exempt']) - price) * qty
        if conditional < MIN_CONDITIONAL_NET:
            continue
        selected.append((row, qty, conditional))
        used_items.add(row['item_id'])
        available -= qty * price
    return selected


def issue_picks(state, rows, now, source_manifest):
    if state['source_status'] != 'CAPTURED_WITH_REVIEW' or state['kill_switch'] or state['public_pause']:
        return []
    _expire_paper_windows(state, now)
    existing = [p for p in state['picks'] if p['id'] in state['active_pick_ids']]
    selected = _select(rows, state, now, existing)
    issued = []
    active = [p['id'] for p in existing]
    prior = {p['item_id']: p for p in state['picks'][-100:]}
    for row, qty, conditional in selected:
        buy, sell = row['observed_instant_sell_low'], row['observed_instant_buy_high']
        basis = dict(item_id=row['item_id'], buy=buy, sell=sell, qty=qty, policy=POLICY, issued_at=now)
        pick_id = digest(basis)[:20]
        if any(p['id'] == pick_id for p in state['picks']):
            active.append(pick_id)
            continue
        stresses = []
        for decline_bps in (100, 300, 500):
            exit_price = max(1, sell*(10000-decline_bps)//10000)
            stresses.append(dict(exit_decline_bps=decline_bps, conditional_gp=(exit_price-tax_per_unit(exit_price,row['tax_exempt'])-buy)*qty))
        pick = dict(id=pick_id, parent_id=prior.get(row['item_id'], {}).get('id'), item_id=row['item_id'],
            name=row['name'], policy=POLICY, engine=VERSION, rule_version='public-rules-1',
            source_hashes=[c['sha256'] for c in source_manifest], issued_at=now, first_public_at=None,
            observed_instant_sell_low=buy, observed_instant_buy_high=sell,
            observed_low_time=row['low_time'], observed_high_time=row['high_time'],
            max_buy_offer=buy, target_sell_offer=sell, break_even_sell=break_even_price(buy,row['tax_exempt']),
            quantity_assumption=qty, required_cash=qty*buy, conditional_after_tax_gp=conditional,
            tax_per_unit=tax_per_unit(sell,row['tax_exempt']), tax_exempt=row['tax_exempt'], horizon_seconds=3600,
            expires_at=min(now+PICK_TTL, row['low_time']+300, row['high_time']+300), action='PAPER_NEW_ENTRY',
            status='ISSUED_NOT_YET_PUBLIC', evidence='CONDITIONAL_SCENARIO',
            reason='Recent two-sided trades, reviewed tax rule, named hourly-volume sizing cap, and feasible shared paper cash.' +
                   (' Validated public interval forecast affects research rank only; no fill forecast.'
                    if row.get('ranking_mode') == 'ML_RESEARCH_ACTIVE' else ''),
            risks=['Both sides are reported past transactions; neither is a current executable order book.',
                   'Remaining personal buy allowance and order queue depth are unknown.',
                   'The same-item buy and later sell may remain unfilled or lose GP.'],
            stress=stresses, execution_assumption='NO_FILL_INFERRED',
            exact_price_volume=None, attainable_quantity=None)
        state['picks'].append(pick)
        state['paper_cash'] -= pick['required_cash']
        state['paper_reserved'] += pick['required_cash']
        issued.append(pick)
        active.append(pick_id)
    state['active_pick_ids'] = active
    return issued


def _expire_paper_windows(state, now):
    """Release paper-only reservations even during source outage or pause."""
    active = []
    for pick in state['picks']:
        if pick['id'] not in state['active_pick_ids']:
            continue
        if pick['expires_at'] <= now:
            state['paper_cash'] += pick['required_cash']
            state['paper_reserved'] -= pick['required_cash']
            pick['status'] = 'WINDOW_EXPIRED_EXECUTION_UNKNOWN'
        else:
            active.append(pick['id'])
    state['active_pick_ids'] = active


def grade_picks(state, observations, now):
    """Track later price-path evidence only; never credit an assumed fill."""
    existing = {o['pick_id']: o for o in state['outcomes']}
    first = learning._first_observations(observations, now)
    count = 0
    for pick in state['picks']:
        if pick['first_public_at'] is None or pick['status'] == 'PUBLISHED_AFTER_EXPIRY':
            continue
        previous = existing.get(pick['id'])
        if previous and previous['status'] == 'PRICE_PATH_OBSERVED':
            continue
        evaluation_at = pick['first_public_at'] + 60 + pick['horizon_seconds']
        if now < evaluation_at:
            continue
        later = [o for (item, bucket), o in first.items() if item == pick['item_id'] and
                 bucket >= evaluation_at and bucket < evaluation_at + 3600 and o['received_at'] <= now]
        observation = min(later, key=lambda o: o['bucket_start']) if later else None
        if observation is None and (now < evaluation_at + 3600 or previous):
            continue
        outcome = dict(pick_id=pick['id'], evaluated_at=now, evaluation_clock=evaluation_at,
            status='PRICE_PATH_OBSERVED' if observation else 'DATA_GAP',
            later_low_avg=observation['low_avg'] if observation else None,
            later_high_avg=observation['high_avg'] if observation else None,
            later_low_units=observation['low_units'] if observation else None,
            later_high_units=observation['high_units'] if observation else None,
            hypothetical_fill=None, realized_simulated_gp=None, actual_gp=None,
            reference_mark_gp=((int(observation['low_avg']) - tax_per_unit(int(observation['low_avg']),
                pick['tax_exempt']) - pick['max_buy_offer'])*pick['quantity_assumption']
                if observation and observation.get('low_avg') is not None else None),
            note='Full-quantity lower-side reference mark is a stress scenario; later averages and units do not establish fills or profit.')
        outcome['parent_id'] = previous['id'] if previous else None
        outcome['id'] = digest(outcome)[:20]
        state['outcomes'].append(outcome)
        count += 1
    return count


def _append_observations(state, five, capture_hash, received_at, selected_ids, provenance='CAPTURED_5M'):
    latest = {}
    for observation in sorted(state['observations'], key=lambda o:o['received_at']):
        latest[(observation['item_id'], observation['bucket_start'])] = observation
    for ident in selected_ids:
        row = five['items'].get(str(ident))
        if row is None:
            continue
        prior = latest.get((ident, five['start']))
        # A -> B -> A is three point-in-time revisions. Identical consecutive
        # retrievals remain one observation and never double-count volume.
        if prior is None or prior['capture_hash'] != capture_hash:
            state['observations'].append(dict(item_id=ident, bucket_start=five['start'],
                low_avg=row['low_avg'], high_avg=row['high_avg'],
                low_units=row['low_units'], high_units=row['high_units'],
                received_at=received_at, capture_hash=capture_hash, source=provenance))


def record_shadow_decision(state, rows, five, now):
    model = next((m for m in reversed(state['models']) if m['status'] == 'SHADOW'), None)
    if model is None or now >= five['start'] + 600 or five['start'] % 3600:
        return False
    if any(d['model_id'] == model['id'] and d['bucket_start'] == five['start'] for d in state['shadow_decisions']):
        return False
    candidates = []
    bar_ids = {int(i) for i in five['items']}
    prior = learning._first_observations(state['observations'], now)
    for row in rows:
        if row['flags'] or row['item_id'] not in bar_ids:
            continue
        bar = five['items'][str(row['item_id'])]
        x = learning.feature_vector(bar, prior.get((row['item_id'], five['start']-300)))
        if x is None:
            continue
        forecast = learning.predict(model, x)
        candidates.append((row, forecast['price_return_bps']))
    def allocate(order):
        cash, plans = BANKROLL, []
        for row, _ in order:
            buy = row['observed_instant_sell_low']
            qty = min(row['quantity_assumption'], cash//buy)
            if qty > 0 and len(plans) < MAX_SLOTS:
                plans.append(dict(item_id=row['item_id'], buy=buy, quantity=qty))
                cash -= qty*buy
        return plans
    baseline = allocate(sorted(candidates, key=lambda pair: -pair[0]['conditional_after_tax_gp']))
    candidate = allocate(sorted(candidates, key=lambda pair: -(pair[0]['conditional_after_tax_gp'] +
                 pair[1]*pair[0]['required_cash']/10000)))
    if not baseline or not candidate:
        return False
    state['shadow_decisions'].append(dict(model_id=model['id'], bucket_start=five['start'],
        target_bucket=five['start']+300, issued_at=now, capital=BANKROLL, slots=MAX_SLOTS,
        baseline=baseline, candidate=candidate,
        assumption='Full reference-price acquisition, next-bucket lower-side average mark; neither fill nor profit.'))
    return True


def compact_history(state, archive_dir, now):
    """Keep recent public detail in state and durable older detail in daily gzip.

    The archive is public-only and is committed with the state on the data branch.
    A retry merges by stable ID, so a crash after writing the archive is safe.
    """
    if archive_dir is None:
        if len(state['picks']) > 1000:
            state['public_pause'] = True
        return
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    done = {o['pick_id'] for o in state['outcomes'] if o['status'] == 'PRICE_PATH_OBSERVED'}
    old = [p for p in state['picks'][:-120] if p['issued_at'] < now-86400 and
           (p['id'] in done or p['first_public_at'] is None or p['status'] == 'PUBLISHED_AFTER_EXPIRY')]
    if not old:
        return
    by_day = {}
    old_ids = {p['id'] for p in old}
    archived_outcomes = [o for o in state['outcomes'] if o['pick_id'] in old_ids]
    for pick in old:
        day = datetime.fromtimestamp(pick['issued_at'], timezone.utc).strftime('%Y-%m-%d')
        by_day.setdefault(day, []).append(dict(type='pick', body=pick))
    for outcome in archived_outcomes:
        pick = next(p for p in old if p['id'] == outcome['pick_id'])
        day = datetime.fromtimestamp(pick['issued_at'], timezone.utc).strftime('%Y-%m-%d')
        by_day.setdefault(day, []).append(dict(type='outcome', body=outcome))
    for day, new_records in by_day.items():
        path = archive_dir / (day + '.jsonl.gz')
        existing = []
        if path.exists():
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                existing = [json.loads(line) for line in handle]
        keyed = {(r['type'], r['body'].get('id', r['body'].get('pick_id'))): r for r in existing+new_records}
        temporary = path.with_name(path.name+'.new')
        with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=6) as handle:
            for key in sorted(keyed):
                handle.write(json.dumps(keyed[key], sort_keys=True, separators=(',', ':'))+'\n')
        os.replace(temporary, path)
    state['picks'] = [p for p in state['picks'] if p['id'] not in old_ids]
    state['outcomes'] = [o for o in state['outcomes'] if o['pick_id'] not in old_ids]
    state['archived_counts']['picks'] += len(old)
    state['archived_counts']['outcomes'] += len(archived_outcomes)
    if sum(p.stat().st_size for p in archive_dir.glob('*.gz')) > 50_000_000:
        state['public_pause'] = True
        state['source_error'] = 'PUBLIC_ARCHIVE_LIMIT'


def cycle(state, client, rules, now, run_id, archive_dir=None):
    validate_state(state)
    if run_id in state['run_ids']:
        return dict(status='IDEMPOTENT_REPLAY', run_id=run_id)
    state['run_ids'].append(run_id)
    state['run_ids'] = state['run_ids'][-1000:]
    state['last_run_at'] = now
    _expire_paper_windows(state, now)
    if utc(now)[:10] > rules['valid_until']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'PUBLIC_RULE_REVIEW_EXPIRED'
        state['learning_status'] = 'PAUSED'
        return dict(status='PAUSED', error='PUBLIC_RULE_REVIEW_EXPIRED', run_id=run_id)
    if state['kill_switch']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'KILL_SWITCH'
        state['learning_status'] = 'PAUSED'
        return dict(status='PAUSED', run_id=run_id)
    result = collect(state, client, now)
    if not result['ok']:
        state['learning_status'] = 'PAUSED'
        state['learning_reason'] = dict(status='PAUSED', reason='SOURCE_UNAVAILABLE')
        return dict(status=state['source_status'], error=result['error'], run_id=run_id)
    data = result['data']
    active_model = next((m for m in state['models'] if m['id'] == state['active_model_id'] and
                         m['status'] == 'RESEARCH_ACTIVE'), None)
    rows = screen(state['mapping'], data['latest'], data['5m'], data['1h'], rules, now,
                  state.get('daily_volume'), active_model)
    if state.get('chart_budget_day') != now//86400:
        state['chart_budget_day'], state['chart_budget_used'] = now//86400, 0
    # At most sixteen historical chart requests per UTC day. These downloads
    # have reconstructed availability and never become prospective ML labels.
    for row in [r for r in rows if not r['flags']][:4]:
        if state['chart_budget_used'] >= 16:
            break
        key = str(row['item_id'])
        cached = state['history_cache'].get(key)
        if cached and now-cached['received_at'] < 86400:
            continue
        state['chart_budget_used'] += 1
        try:
            raw, meta = client.fetch('timeseries', {'id': row['item_id'], 'lookback': '7d'}, now=now)
            series = parse_timeseries(raw, meta['received_at'], row['item_id'])
            state['history_cache'][key] = dict(**series, received_at=meta['received_at'], source_hash=meta['sha256'])
            state['capture_metadata'].append(meta)
        except ValidationError as exc:
            state.setdefault('history_errors', {})[key] = str(exc)
    state['history_cache'] = {k:v for k,v in state['history_cache'].items() if now-v['received_at'] < 8*86400}
    selected_ids = [r['item_id'] for r in rows if not r['flags']][:5]
    selected_ids += [p['item_id'] for p in state['picks'] if p['first_public_at'] and p['issued_at'] >= now - OBS_RETENTION]
    for key, five in data.items():
        if key == '5m' or key.startswith('5m:'):
            meta = next((c for c in result['captures'] if c['kind'] == '5m' and
                         (key == '5m' and '?' not in c['url'] or key != '5m' and str(five['start']) in c['url'])), None)
            if meta:
                _append_observations(state, five, meta['sha256'], meta['received_at'], set(selected_ids),
                                     'CAPTURED_5M' if key == '5m' else 'RETROSPECTIVE_BACKFILL')
    state['observations'] = [o for o in state['observations'] if o['bucket_start'] >= now - OBS_RETENTION]
    grade_picks(state, state['observations'], now)
    state['predictions'] = [p for p in state['predictions'] if p['issued_at'] >= now - OBS_RETENTION]
    state['shadow_decisions'] = [d for d in state['shadow_decisions'] if d['issued_at'] >= now - OBS_RETENTION]
    learning.daily_readiness(state, now)
    first_for_features = learning._first_observations(state['observations'], now)
    current_for_ml = [dict(item_id=item_id, bucket_start=data['5m']['start'],
                           previous=first_for_features.get((item_id, data['5m']['start']-300)),
                           **data['5m']['items'][str(item_id)])
                      for item_id in selected_ids if str(item_id) in data['5m']['items']]
    learning.record_predictions(state, current_for_ml, now)
    record_shadow_decision(state, rows, data['5m'], now)
    issued = issue_picks(state, rows, now, result['captures'])
    compact_history(state, archive_dir, now)
    if state['public_pause']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = state['source_error'] or 'PUBLIC_PAUSE'
        state['learning_status'] = 'PAUSED'
    state['latest_screen'] = rows
    state['latest_screen_at'] = now
    state['latest_capture_hashes'] = [c['sha256'] for c in result['captures']]
    raw_due = state.get('last_raw_evidence_day') != now//86400
    if raw_due:
        state['last_raw_evidence_day'] = now//86400
    return dict(status=state['source_status'], run_id=run_id, screened=len(rows),
                eligible=sum(not r['flags'] for r in rows), issued=len(issued),
                learning_status=state['learning_status'], source_calls=client.calls,
                source_raw_bytes=client.raw_bytes, raw_evidence_due=raw_due)


def mark_published(state, pick_ids, deployed_at, deployment_id, url):
    if any(r['deployment_id'] == deployment_id for r in state['publication_receipts']):
        return 0
    changed = 0
    for pick in state['picks']:
        if pick['id'] in pick_ids and pick['first_public_at'] is None:
            pick['first_public_at'] = deployed_at
            if deployed_at < pick['expires_at']:
                pick['status'] = 'PUBLIC_FORWARD_PAPER'
                pick['evidence'] = 'FORWARD_PAPER'
            else:
                pick['status'] = 'PUBLISHED_AFTER_EXPIRY'
            changed += 1
    state['publication_receipts'].append(dict(deployment_id=deployment_id, deployed_at=deployed_at,
        url=url, pick_ids=sorted(pick_ids), acknowledged=changed))
    state['publication_receipts'] = state['publication_receipts'][-1000:]
    return changed


PUBLIC_KEYS = {'schema', 'generated_at', 'source_status', 'source_error', 'source_success_at', 'run_at',
               'quote_valid_until', 'bankroll', 'live_pilot_ceiling', 'cash_available', 'cash_reserved',
               'top_picks', 'screener', 'graphs', 'history', 'outcomes', 'learning', 'coverage',
               'versions', 'publication', 'methodology', 'performance'}


def public_export(state, now):
    validate_state(state)
    fresh = state['source_status'] == 'CAPTURED_WITH_REVIEW' and state.get('latest_screen_at') is not None and now - state['latest_screen_at'] <= 300
    picks = [p for p in state['picks'] if p['id'] in state['active_pick_ids'] and p['expires_at'] > now] if fresh else []
    # An explicit field allowlist blocks accidental export of private receipt() or
    # future state fields. This file never reads the private ledger.
    allowed_pick = ('id', 'parent_id', 'item_id', 'name', 'policy', 'engine', 'rule_version',
        'issued_at', 'first_public_at', 'observed_instant_sell_low', 'observed_instant_buy_high',
        'observed_low_time', 'observed_high_time', 'max_buy_offer', 'target_sell_offer',
        'break_even_sell', 'quantity_assumption', 'required_cash', 'conditional_after_tax_gp',
        'tax_per_unit', 'tax_exempt', 'horizon_seconds', 'expires_at', 'action', 'status', 'evidence',
        'reason', 'risks', 'stress', 'execution_assumption', 'exact_price_volume', 'attainable_quantity')
    clean = lambda p: {k: p.get(k) for k in allowed_pick}
    histories = [clean(p) for p in state['picks'][-120:]]
    top = [clean(p) for p in picks][:4]
    screen_keys = ('item_id', 'name', 'observed_instant_sell_low', 'observed_instant_buy_high',
        'low_age', 'high_age', 'five_low_units', 'five_high_units', 'hour_low_units',
        'hour_high_units', 'day_low_units', 'day_high_units', 'public_limit', 'quantity_assumption', 'required_cash',
        'conditional_after_tax_gp', 'status', 'flags')
    screener = [{k: r.get(k) for k in screen_keys} for r in state.get('latest_screen', [])] if fresh else []
    graphs = {}
    first = learning._first_observations(state['observations'], now)
    for p in top:
        captured = [dict(bucket_start=o['bucket_start'], low_avg=o['low_avg'], high_avg=o['high_avg'],
                       low_units=o['low_units'], high_units=o['high_units'])
                  for (item, _), o in first.items() if item == p['item_id']]
        cache = state['history_cache'].get(str(p['item_id']))
        points = cache['points'] if cache else captured
        graphs[str(p['item_id'])] = dict(stats=graph_stats(points),
            points=sorted(points, key=lambda x: x['bucket_start'])[-288:],
            resolution_seconds=cache['timestep'] if cache else 300,
            availability=cache['availability'] if cache else 'FIRST_HAND_CAPTURE',
            received_at=cache['received_at'] if cache else None,
            captured_recent_points=len(captured))
    model = next((m for m in reversed(state['models']) if m['status'] in ('SHADOW', 'RESEARCH_ACTIVE')), None)
    learning_view = dict(status=state['learning_status'], active_model_id=state['active_model_id'],
        candidate_model_id=model['id'] if model else None, model_version=model['version'] if model else None,
        last_training_at=state['last_training_at'], last_evaluation_at=state['last_evaluation_at'],
        latest_data_cutoff=state['latest_data_cutoff'],
        samples=model['samples'] if model else None, metrics=model['metrics'] if model else None,
        shadow=state.get('shadow_metrics', {}).get(model['id']) if model else None,
        promotion_gate=state.get('promotion_gate'),
        evidence_scope='Public interval averages and reported units only. No personal execution labels or verified trading edge.')
    body = dict(schema='osrs-public-site-1', generated_at=now,
        source_status=state['source_status'] if state['source_status'] != 'CAPTURED_WITH_REVIEW'
                      else ('CAPTURED_WITH_REVIEW' if fresh else 'STALE_OR_UNAVAILABLE'),
        source_error=state['source_error'], source_success_at=state['last_source_success_at'],
        run_at=state['last_run_at'], quote_valid_until=min((p['expires_at'] for p in picks), default=None),
        bankroll=BANKROLL, live_pilot_ceiling=LIVE_PILOT_CEILING,
        cash_available=state['paper_cash'],
        cash_reserved=state['paper_reserved'], top_picks=top,
        screener=screener, graphs=graphs, history=histories,
        outcomes=state['outcomes'][-120:], learning=learning_view,
        coverage=dict(screened=len(screener), eligible=sum(r['status']=='ELIGIBLE_RESEARCH' for r in screener),
                      captured_observations=len(state['observations']), archived_counts=state['archived_counts']),
        versions=dict(pipeline=VERSION, policy=POLICY, rules='public-rules-1'),
        publication=state['publication_receipts'][-1] if state['publication_receipts'] else None,
        performance=dict(conditional_active_portfolio_gp=sum(p['conditional_after_tax_gp'] for p in picks),
            simulated_realized_gp=0, simulated_inventory_at_cost=0,
            uncertain_reference_equity=None,
            forward_price_paths_graded=sum(o['status']=='PRICE_PATH_OBSERVED' for o in state['outcomes']),
            forward_negative_reference_marks=sum(o.get('reference_mark_gp') is not None and o['reference_mark_gp'] < 0
                                                 for o in state['outcomes']),
            historical_simulation_status='DATA_INSUFFICIENT',
            actual_user_recorded_status='PRIVATE_NOT_CONNECTED',
            note='No fill model is activated. Cash reservations are hypothetical order windows; no purchase or sale is credited.'),
        methodology='All top-pick GP is conditional on full assumed same-item buy and later sell. Market volume is not queue depth; no fills or real profit are inferred.')
    if set(body) != PUBLIC_KEYS:
        raise AssertionError('Public export allowlist drift.')
    return body


def write_export(path, body):
    raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('init', 'cycle', 'publish', 'export', 'rollback'))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--raw-dir', type=Path)
    parser.add_argument('--archive-dir', type=Path)
    parser.add_argument('--review', type=Path, default=ROOT/'config'/'public-source-review.json')
    parser.add_argument('--rules', type=Path, default=ROOT/'config'/'public-rules.json')
    parser.add_argument('--run-id')
    parser.add_argument('--pick-ids', default='')
    parser.add_argument('--deployment-id')
    parser.add_argument('--deployment-url')
    args = parser.parse_args(argv)
    now = int(time.time())
    if args.operation == 'init':
        if args.state.exists():
            raise ValidationError('Durable state exists; refusing overwrite.')
        state = new_state(now)
    else:
        state = load_state(args.state)
    result = {}
    if args.operation == 'cycle':
        if not args.run_id or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', args.run_id):
            raise ValidationError('Stable run ID required.')
        contract = SourceContract.from_dict(strict_json(args.review.read_bytes()))
        client = SourceClient(contract, args.raw_dir)
        result = cycle(state, client, load_rules(args.rules), now, args.run_id, args.archive_dir)
    elif args.operation == 'publish':
        if not args.deployment_id or not args.deployment_url:
            raise ValidationError('Deployment receipt required.')
        count = mark_published(state, set(filter(None, args.pick_ids.split(','))), now,
                               args.deployment_id, args.deployment_url)
        result = dict(published=count)
    elif args.operation == 'rollback':
        learning.rollback(state)
        result = dict(status='RULES_BASELINE')
    if args.operation != 'export':
        result['state_sha256'] = save_state(args.state, state)
    if args.output:
        result['export_sha256'] = write_export(args.output, public_export(state, now))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
