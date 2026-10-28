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
from email.utils import parsedate_to_datetime
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
RESEARCH_POLICY = 'ml-research-rank-2'
HOST = 'prices.runescape.wiki'
BANKROLL = 500_000_000
LIVE_PILOT_CEILING = 50_000_000
MAX_POSITION = 125_000_000
MAX_SLOTS = 4
MIN_CONDITIONAL_NET = 50_000
PICK_TTL = 600
OBS_RETENTION = 10 * 86400
GAP_REVISION_GRACE = 86400
MAX_STATE_BYTES = 12_000_000
MAX_EXPORT_BYTES = 8_000_000
MAX_GRAPH_ITEMS = 32
MAX_FETCHES = 18
MAX_RETRIES = 2
MAX_INLINE_RETRY_SECONDS = 8
MAX_FETCH_WALL_SECONDS = 90
MAX_RAW_TOTAL = 18_000_000
MAX_SAFE_INTEGER = 2**53 - 1
VERSION = 'public-pipeline-2'
OPTIONAL_STATE_KEYS = {'hourly', 'hourly_at', 'last_24h_at', 'daily_volume', 'data_gap',
                       'history_errors', 'learning_reason', 'latest_screen',
                       'latest_source_contract_sha256', 'latest_rule_sha256',
                       'last_training_duration_ms',
                       'latest_screen_at', 'latest_capture_hashes', 'chart_budget_day',
                       'chart_budget_used', 'recent_revision_bucket', 'last_revision_scan_at',
                       'route_counts'}
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
                research_cohort_index=None, research_cohort_ids=[],
                last_source_success_at=None, last_run_at=None, run_ids=[],
                last_run_id=None,
                latest_rule_version=None,
                last_run_end_at=None, last_run_event=None,
                last_automatic_run_at=None, last_automatic_source_success_at=None,
                last_automatic_publication_at=None,
                source_status='DATA_UNAVAILABLE', source_error='NO_CAPTURE_YET',
                last_raw_evidence_day=None,
                circuit_open_until=0, kill_switch=False, public_pause=False,
                models=[], predictions=[], last_learning_check=0,
                shadow_decisions=[],
                learning_status='DATA_INSUFFICIENT', active_model_id=None,
                last_training_at=None, last_evaluation_at=None, latest_data_cutoff=None,
                shadow_metrics={}, promotion_gate=None, portfolio_shadow_comparison=None,
                economic_shadow_comparison=None,
                publication_receipts=[], pending_publication=None, active_pick_ids=[])


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
    pending = state.get('pending_publication')
    if pending is not None and (not isinstance(pending, dict) or
        set(pending) != {'deployment_id', 'artifact_id', 'export_sha256', 'pick_ids', 'run_event'} or
        not re.fullmatch(r'\d+-\d+', str(pending['deployment_id'])) or
        not re.fullmatch(r'[0-9a-f]{32}', str(pending['artifact_id'])) or
        not re.fullmatch(r'[0-9a-f]{64}', str(pending['export_sha256'])) or
        not isinstance(pending['pick_ids'], list) or
        pending['run_event'] not in ('push', 'schedule', 'workflow_dispatch')):
        raise ValidationError('Invalid pending public deployment identity.')
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


def _retry_delay(value, attempt, now=None):
    """Return the provider's entire requested wait, including HTTP-date waits."""
    if value is not None:
        header = str(value).strip()
        if re.fullmatch(r'\d+', header):
            return int(header)
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, math.ceil(date.timestamp() - (time.time() if now is None else now)))
        except (TypeError, ValueError, OverflowError):
            pass
    return min(8, 2 ** attempt) + random.Random(attempt).random()


class ProviderDeferred(ValidationError):
    def __init__(self, retry_at, reason):
        super().__init__(reason)
        self.retry_at = int(math.ceil(retry_at))


class SourceClient:
    def __init__(self, contract, raw_dir=None, opener=None, sleeper=time.sleep, clock=time.time):
        self.contract = contract
        self.opener = opener or urllib.request.build_opener(NoRedirects())
        self.sleeper = sleeper
        self.clock = clock
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self.calls = 0
        self.raw_bytes = 0

    def fetch(self, kind, params=None):
        if kind not in ('mapping', 'latest', '5m', '1h', '24h', 'timeseries'):
            raise ValidationError('Unsupported source route.')
        if self.calls >= MAX_FETCHES:
            raise ValidationError('Source request budget exhausted.')
        version = self.contract.version_for(kind)
        query = urllib.parse.urlencode(params or {})
        url = f'https://{HOST}/api/{version}/osrs/{kind}' + ('?' + query if query else '')
        fetch_started = self.clock()
        for attempt in range(MAX_RETRIES + 1):
            if self.calls >= MAX_FETCHES:
                raise ValidationError('Source request budget exhausted.')
            self.calls += 1
            requested_at = int(self.clock())
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
                    received_at = int(self.clock())
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
                if exc.code not in (429, 500, 502, 503, 504):
                    raise ValidationError(f'HTTP_{exc.code}') from exc
                delay = _retry_delay(exc.headers.get('Retry-After'), attempt, self.clock())
                if delay > MAX_INLINE_RETRY_SECONDS or self.clock() - fetch_started + delay > MAX_FETCH_WALL_SECONDS:
                    raise ProviderDeferred(self.clock() + delay, f'HTTP_{exc.code}_RETRY_DEFERRED') from exc
                if attempt == MAX_RETRIES:
                    raise ProviderDeferred(self.clock() + delay,
                                           f'HTTP_{exc.code}_RETRIES_EXHAUSTED') from exc
                self.sleeper(delay)
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt == MAX_RETRIES:
                    raise ValidationError('TRANSIENT_NETWORK_FAILURE: ' + type(exc).__name__) from exc
                delay = _retry_delay(None, attempt, self.clock())
                if self.clock() - fetch_started + delay > MAX_FETCH_WALL_SECONDS:
                    raise ProviderDeferred(self.clock() + delay, 'TRANSIENT_NETWORK_RETRY_DEFERRED') from exc
                self.sleeper(delay)
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
            raw, meta = client.fetch(kind)
            parsed = _parse_safe(kind, raw, meta['received_at'], client.contract)
            if not parsed['items']:
                raise ValidationError(kind.upper() + '_NO_USABLE_ITEMS')
            accepted=len(parsed['items'])
            floor=(client.contract.minimum_item_counts or {}).get(kind,0)
            previous=(state.get('route_counts') or {}).get(kind,0)
            if accepted<max(floor,previous//2):
                raise ValidationError(kind.upper()+'_COVERAGE_COLLAPSE')
            fetched[kind] = parsed
            meta['accepted_items'] = accepted
            meta['quarantined_items'] = len(parsed['rejected'])
            captures.append(meta)
        if '1h' not in fetched:
            fetched['1h'] = state['hourly']
        # The bulk latest 5m response is a publication watermark. Backfill only
        # documented timestamp requests and at most twelve missing buckets.
        current = fetched['5m']['start']
        previous = state.get('last_5m_bucket')
        if previous is not None and current < previous:
            raise ValidationError('SOURCE_WATERMARK_REGRESSED')
        if previous is not None and current > previous + 300:
            missing = list(range(previous + 300, current, 300))
            if len(missing) > 12:
                state['data_gap'] = dict(from_bucket=missing[0], to_bucket=missing[-1], reason='BACKFILL_BOUND')
                missing = missing[-12:]
            for bucket in missing:
                raw, meta = client.fetch('5m', {'timestamp': bucket})
                parsed = _parse_safe('5m', raw, meta['received_at'], client.contract)
                if parsed['start'] != bucket:
                    raise ValidationError('Backfill timestamp mismatch.')
                fetched['5m:' + str(bucket)] = parsed
                captures.append(meta)
        if previous is not None and current >= previous and now - state.get('last_revision_scan_at', 0) >= 3600:
            bucket = current - 300
            if bucket >= 0 and '5m:' + str(bucket) not in fetched:
                raw, meta = client.fetch('5m', {'timestamp': bucket})
                parsed = _parse_safe('5m', raw, meta['received_at'], client.contract)
                if parsed['start'] != bucket:
                    raise ValidationError('Revision timestamp mismatch.')
                fetched['5m:' + str(bucket)] = parsed
                captures.append(meta)
            state['last_revision_scan_at'] = max(now, captures[-1]['received_at']) if captures else now
        capture_at = max((c['received_at'] for c in captures), default=now)
        state['source_status'] = 'CAPTURED_WITH_REVIEW'
        state['source_error'] = None
        state['last_source_success_at'] = capture_at
        state['last_5m_bucket'] = current
        state['route_counts']={**(state.get('route_counts') or {}),
                               **{kind:len(fetched[kind]['items']) for kind in routes if kind in fetched}}
        if 'mapping' in fetched:
            state['mapping'] = fetched['mapping']['items']
            state['mapping_at'] = next(c['received_at'] for c in captures if c['kind'] == 'mapping')
        if '1h' in routes:
            state['hourly'] = fetched['1h']
            state['hourly_at'] = next(c['received_at'] for c in captures if c['kind'] == '1h')
        if '24h' in fetched:
            state['last_24h_at'] = next(c['received_at'] for c in captures if c['kind'] == '24h')
            state['daily_volume'] = {key: [value.get('low_units'), value.get('high_units')]
                                     for key, value in fetched['24h']['items'].items()}
        state['circuit_open_until'] = 0
        state['capture_metadata'].extend(captures)
        state['capture_metadata'] = [c for c in state['capture_metadata'] if c['received_at'] >= capture_at - OBS_RETENTION][-2000:]
        return dict(ok=True, data=fetched, captures=captures)
    except ValidationError as exc:
        message = str(exc)
        failure_at = max(now, int(client.clock())) if hasattr(client, 'clock') else now
        state['source_status'] = 'DATA_GAP' if (isinstance(exc, ProviderDeferred) or
            message.startswith('TRANSIENT_') or message.startswith('HTTP_5') or
            message.startswith('SOURCE_WATERMARK_') or message.endswith('_COVERAGE_COLLAPSE') or
            message == 'HTTP_429') else 'PAUSED'
        state['source_error'] = message
        if isinstance(exc, ProviderDeferred):
            state['circuit_open_until'] = max(failure_at, exc.retry_at)
        elif message.startswith('HTTP_403') or message.startswith('HTTP_401') or state['source_status'] == 'PAUSED':
            state['circuit_open_until'] = failure_at + 86400
        elif message == 'HTTP_429':
            state['circuit_open_until'] = failure_at + 1800
        elif state['source_status'] == 'DATA_GAP':
            state['circuit_open_until'] = failure_at + 300
        state['capture_metadata'].extend(captures)
        state['capture_metadata'] = state['capture_metadata'][-2000:]
        return dict(ok=False, error=message, captures=captures)


def _tax_status(name):
    # The named exclusions are from the GE exemption list. Any uncertain variant
    # is excluded; the ordinary rule is applied only to reviewed IDs in config.
    return 'UNREVIEWED'


def load_rules(path):
    rules = strict_json(Path(path).read_bytes())
    if not isinstance(rules, dict) or rules.get('version') not in ('public-rules-1', 'public-rules-2'):
        raise ValidationError('Invalid public item rules.')
    if not isinstance(rules.get('ordinary_ids'), list) or not isinstance(rules.get('exempt_ids'), list):
        raise ValidationError('Invalid public rule lists.')
    if set(rules['ordinary_ids']) & set(rules['exempt_ids']):
        raise ValidationError('Conflicting tax rules.')
    if rules['version'] == 'public-rules-2':
        registry = rules.get('exempt_registry')
        exclusions = rules.get('special_exclusions')
        verified_groups = rules.get('verified_item_only_registry', {})
        if not isinstance(registry, dict) or not isinstance(exclusions, dict) or not rules.get('general_ordinary_rule'):
            raise ValidationError('Incomplete general tax rule and exception registry.')
        if not isinstance(verified_groups, dict):
            raise ValidationError('Invalid verified item-only limit registry.')
        for ident, name in verified_groups.items():
            if not re.fullmatch(r'[1-9]\d*', ident) or not isinstance(name,str) or not name:
                raise ValidationError('Invalid verified item-only limit entry.')
        for ident, name in {**registry, **exclusions}.items():
            if not re.fullmatch(r'[1-9]\d*', ident) or not isinstance(name, str) or not name:
                raise ValidationError('Invalid public tax exception registry.')
        if set(rules['ordinary_ids']) & set(map(int, registry)):
            raise ValidationError('Legacy ordinary tax rule conflicts with reviewed exemption.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rules.get('valid_until', '')):
        raise ValidationError('Public tax-rule expiry required.')
    return rules


def _rule(item_id, rules, meta=None):
    if str(item_id) in rules.get('special_exclusions', {}):
        return None
    registry = rules.get('exempt_registry', {})
    if str(item_id) in registry:
        return True if meta and meta.get('name') == registry[str(item_id)] else None
    if meta and meta.get('name') in registry.values():
        return None  # Exemption name with an unreviewed ID or variant.
    if item_id in rules['ordinary_ids']:
        return False
    if item_id in rules['exempt_ids']:
        return True
    if rules.get('version') == 'public-rules-2' and meta and meta.get('public_limit') is not None:
        return False
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
    peak = 0
    max_downside_bps = 0
    for point in sorted(valid, key=lambda p: p['bucket_start']):
        peak = max(peak, point['low_avg'])
        max_downside_bps = max(max_downside_bps,
                               round(10000 * (peak - point['low_avg']) / peak))
    return dict(coverage=len(valid), lowest_low_average=minimum['low_avg'],
                low_bucket_start=minimum['bucket_start'], low_bucket_units=minimum.get('low_units'),
                exact_units_at_price=None, attainable_quantity=None,
                low_p10=lows[int((len(lows) - 1) * .1)], low_median=lows[len(lows)//2],
                high_p10=highs[int((len(highs)-1)*.1)], high_median=highs[len(highs)//2],
                median_spread=spreads[len(spreads)//2],
                historical_low_drawdown_bps=max_downside_bps,
                reported_low_units=sum(low_units) if low_units else None,
                reported_high_units=sum(high_units) if high_units else None,
                first_bucket=min(p['bucket_start'] for p in valid),
                last_bucket=max(p['bucket_start'] for p in valid),
                note='Bucket averages and whole-bucket units; exact-price units and attainable fills are UNKNOWN.')


def research_rank_score(row, forecast):
    """One versioned two-side stress for shadow and active research ranking."""
    low = row['observed_instant_sell_low']
    high = row['observed_instant_buy_high']
    quantity = row['quantity_assumption']
    conditional = row['conditional_after_tax_gp']
    if not low or not high or not quantity or conditional is None or row['tax_exempt'] is None:
        return None
    future_low = max(1, round(low*(1+forecast['price_return_bps']/10000)))
    future_high = max(1, round(high*(1+forecast['high_price_return_bps']/10000)))
    stressed_buy = max(low, future_low)
    future_net = (future_high-tax_per_unit(future_high,row['tax_exempt'])-stressed_buy)*quantity
    # A cheaper predicted entry never increases the current conditional value.
    return min(conditional, future_net)


def _research_sort_key(row):
    score = row.get('research_rank_score')
    return (bool(row['flags']), -(score if score is not None else -1), row['item_id'])


def screen(mapping, latest, five, hourly, rules, now, daily=None, active_model=None,
           feature_history=None):
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
        activity_ok = bool(bar and hour)
        if hour:
            for side in ('low', 'high'):
                units = hour.get(side + '_units')
                if type(units) is not int or units <= 0:
                    flags.append('HOURLY_' + side.upper() + '_UNITS_UNKNOWN')
                    activity_ok = False
        if not bar or not hour or not all(type(bar.get(side + '_units')) is int and bar[side + '_units'] > 0 and
                                           type(hour.get(side + '_units')) is int and hour[side + '_units'] > 0 for side in ('low', 'high')):
            flags.append('TWO_SIDED_ACTIVITY_UNKNOWN')
            activity_ok = False
        limit = meta.get('public_limit') if meta else None
        if limit is None:
            flags.append('PUBLIC_LIMIT_UNKNOWN')
        # Wiki documents connected limits for potion doses, while mapping has
        # no group key. Charged/dose variants remain visible but unqualified.
        if meta and re.search(r'\([1-8]\)$', meta['name']):
            flags.append('SHARED_LIMIT_GROUP_UNREVIEWED')
        exempt = _rule(item_id, rules, meta)
        if exempt is None:
            flags.append('SPECIAL_MECHANIC_EXCLUDED' if str(item_id) in rules.get('special_exclusions', {})
                         else 'TAX_RULE_UNREVIEWED')
        quantity, conditional = 0, None
        if low and high and activity_ok and limit and exempt is not None:
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
        preference_flags = ['BELOW_LEGACY_BASELINE_MINIMUM'] if conditional is not None and conditional < MIN_CONDITIONAL_NET else []
        day_units = (daily or {}).get(ident, [None, None])
        row = dict(item_id=item_id, name=meta['name'] if meta else 'Unknown item ' + ident,
                   members=meta.get('members') if meta else None, public_limit=limit,
                   limit_group_status=('UNREVIEWED_CHARGED_VARIANT' if meta and
                                       re.search(r'\([1-8]\)$', meta['name']) else
                                       ('VERIFIED_ITEM_ONLY' if meta and
                                        rules.get('verified_item_only_registry',{}).get(ident)==meta['name']
                                        else 'ITEM_ONLY_ASSUMED')),
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
                   rule_version=rules['version'],
                   rule_status='EXEMPT' if exempt is True else ('ORDINARY' if exempt is False else 'UNREVIEWED'),
                   legacy_baseline_supported=item_id in rules['ordinary_ids'] or item_id in rules['exempt_ids'],
                   preference_flags=preference_flags,
                   flags=sorted(set(flags)), status='ELIGIBLE_RESEARCH' if not flags else 'EXCLUDED')
        row['ranking_mode'] = 'RULES_BASELINE'
        row['research_rank_score'] = conditional
        if active_model is not None and bar and not flags:
            previous = (feature_history or {}).get((item_id, five['start']-300))
            feature = learning.feature_vector(bar, previous)
            if feature is not None:
                forecast = learning.predict(active_model, feature)
                row['research_rank_score'] = research_rank_score(row,forecast)
                row['ranking_mode'] = 'ML_RESEARCH_ACTIVE'
        rows.append(row)
    rows.sort(key=_research_sort_key)
    return rows


def _select(rows, state, now, existing):
    if state['kill_switch'] or state['public_pause']:
        return []
    available = state['paper_cash']
    selected, used_items = [], set()
    used_items.update(p['item_id'] for p in existing)
    for row in rows:
        if (row['flags'] or not row.get('legacy_baseline_supported', True) or
                (row.get('ranking_mode')=='ML_RESEARCH_ACTIVE' and
                 row.get('research_rank_score',row.get('conditional_after_tax_gp',0))<=0) or
                row['item_id'] in used_items or len(selected) + len(existing) >= MAX_SLOTS):
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
        pick_policy = RESEARCH_POLICY if row.get('ranking_mode') == 'ML_RESEARCH_ACTIVE' else POLICY
        basis = dict(item_id=row['item_id'], buy=buy, sell=sell, qty=qty, policy=pick_policy, issued_at=now)
        pick_id = digest(basis)[:20]
        if any(p['id'] == pick_id for p in state['picks']):
            active.append(pick_id)
            continue
        stresses = []
        for decline_bps in (100, 300, 500):
            exit_price = max(1, sell*(10000-decline_bps)//10000)
            stresses.append(dict(exit_decline_bps=decline_bps, conditional_gp=(exit_price-tax_per_unit(exit_price,row['tax_exempt'])-buy)*qty))
        pick = dict(id=pick_id, parent_id=prior.get(row['item_id'], {}).get('id'), item_id=row['item_id'],
            name=row['name'], policy=pick_policy, engine=VERSION, rule_version=row.get('rule_version','public-rules-1'),
            source_hashes=[c['sha256'] for c in source_manifest], issued_at=now, first_public_at=None,
            evidence_capsule=dict(public_limit=row.get('public_limit'),
                limit_group_status=row.get('limit_group_status'),
                five_low_units=row.get('five_low_units'),five_high_units=row.get('five_high_units'),
                hour_low_units=row.get('hour_low_units'),hour_high_units=row.get('hour_high_units'),
                source_captures=[{k:c.get(k) for k in ('kind','version','requested_at',
                    'received_at','sha256')} for c in source_manifest],
                raw_payload_retention_days=7),
            observed_instant_sell_low=buy, observed_instant_buy_high=sell,
            observed_low_time=row['low_time'], observed_high_time=row['high_time'],
            max_buy_offer=buy, target_sell_offer=sell, break_even_sell=break_even_price(buy,row['tax_exempt']),
            quantity_assumption=qty, required_cash=qty*buy, conditional_after_tax_gp=conditional,
            tax_per_unit=tax_per_unit(sell,row['tax_exempt']), tax_exempt=row['tax_exempt'], horizon_seconds=3600,
            expires_at=min(now+PICK_TTL, row['low_time']+300, row['high_time']+300), action='PAPER_NEW_ENTRY',
            status='ISSUED_NOT_YET_PUBLIC', evidence='CONDITIONAL_SCENARIO',
            reason='Recent two-sided trades, reviewed tax rule, named hourly-volume sizing cap, and feasible shared paper cash.' +
                   (' Public-interval forecast stress on both sides affects research rank only; no fill forecast.'
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


def _append_observations(state, five, capture_hash, received_at, selected_ids,
                         provenance='CAPTURED_5M', route_version=None):
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
                received_at=received_at, capture_hash=capture_hash, source=provenance,
                route_version=route_version or 'UNKNOWN_LEGACY'))


def record_shadow_decision(state, rows, five, now):
    model = next((m for m in reversed(state['models']) if m['status'] in ('SHADOW', 'RESEARCH_ACTIVE')), None)
    if (model is None or state.get('public_pause') or state.get('source_status') != 'CAPTURED_WITH_REVIEW' or
            now + learning.MIN_ACTION_GAP_SECONDS > five['start'] + learning.HORIZON_SECONDS or
            now >= five['start'] + 600 or five['start'] % 3600):
        return False
    if any(d['model_id'] == model['id'] and d['bucket_start'] == five['start'] for d in state['shadow_decisions']):
        return False
    candidates = []
    bar_ids = {int(i) for i in five['items']}
    prior = learning._first_observations(state['observations'], now)
    for row in rows:
        if row['flags'] or not row.get('legacy_baseline_supported', False) or row['item_id'] not in bar_ids:
            continue
        bar = five['items'][str(row['item_id'])]
        x = learning.feature_vector(bar, prior.get((row['item_id'], five['start']-300)))
        if x is None:
            continue
        forecast = learning.predict(model, x)
        scored = dict(row, research_rank_score=research_rank_score(row,forecast),
                      ranking_mode='ML_RESEARCH_ACTIVE')
        candidates.append((row,scored))
    def allocate(order):
        plans=[]
        for row,qty,_ in _select([row for row,_ in order],
                                 {'kill_switch':False,'public_pause':False,'paper_cash':BANKROLL},now,[]):
            buy = row['observed_instant_sell_low']
            plans.append(dict(item_id=row['item_id'], buy=buy,
                    sell=row['observed_instant_buy_high'],quantity=qty,
                    entry_deadline=min(row['low_time']+300,row['high_time']+300),
                    public_limit=row['public_limit'],tax_exempt=row['tax_exempt'],
                    limit_group_status=('VERIFIED_ITEM_ONLY' if row['limit_group_status']=='VERIFIED_ITEM_ONLY'
                                        else 'ASSUMED_ITEM_ONLY')))
        return plans
    baseline = allocate(sorted(candidates,key=lambda pair:_research_sort_key(pair[0])))
    candidate = allocate(sorted(((scored,baseline) for baseline,scored in candidates),
                                key=lambda pair:_research_sort_key(pair[0])))
    if not baseline or not candidate:
        return False
    state['shadow_decisions'].append(dict(model_id=model['id'], run_id=state.get('last_run_id'),
        public_available_at=None, bucket_start=five['start'],
        target_bucket=five['start']+learning.HORIZON_SECONDS, issued_at=now, capital=BANKROLL, slots=MAX_SLOTS,
        baseline=baseline, candidate=candidate,
        assumption='Candidate uses the same capped two-side future-interval stress rank as the active research policy; the comparator is the current active strategy, with the frozen rules baseline as fallback. This is not expected profit. Later interval-cross executions and marks are assumptions, not observed fills.'))
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
    # A published DATA_GAP remains revisable during the one-day grace window.
    # After that it is archived with every revision; later backfill is still
    # retained as source history, never retroactively called a forward fill.
    done = {o['pick_id'] for o in state['outcomes']
            if o['status'] in ('PRICE_PATH_OBSERVED', 'DATA_GAP')}
    old = [p for p in state['picks'][:-120] if p['issued_at'] < now-GAP_REVISION_GRACE and
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


def _effective_now(client, fallback):
    return max(fallback, int(client.clock())) if hasattr(client, 'clock') else fallback


def sample_research_ids(rows, bucket_start, cohort_ids=None):
    """Keep a tiny cohort for two hours so the one-hour target can mature.

    This also bounds ten days of captured observations and predictions within
    the 12 MB durable-state limit. Rotation depends on the clock and eligible
    IDs, never on later labels or forecast performance. A persisted cohort
    survives changes in the eligible pool within its two-hour window.
    """
    eligible = [r['item_id'] for r in rows if not r['flags']]
    pool = sorted(eligible[5:])
    cohort = bucket_start // 7200
    rotation = ([pool[cohort%len(pool)]] if pool else []) if cohort_ids is None else [
        ident for ident in cohort_ids if ident in eligible][:1]
    captured = list(dict.fromkeys(eligible[:5] + rotation))
    predicted = list(dict.fromkeys(eligible[:1] + rotation + eligible[1:3]))[:3]
    return captured, predicted


def pending_pick_observation_ids(picks, decision_at):
    """Retain an issued item's prospective evaluation window, then stop sampling it."""
    return [p['item_id'] for p in picks if p.get('first_public_at') is not None and
            decision_at < p['first_public_at'] + 60 + p['horizon_seconds'] + 3600]


def cycle(state, client, rules, now, run_id, archive_dir=None, run_event='manual'):
    validate_state(state)
    if run_id in state['run_ids']:
        return dict(status='IDEMPOTENT_REPLAY', run_id=run_id)
    state['run_ids'].append(run_id)
    state['run_ids'] = state['run_ids'][-1000:]
    state['last_run_at'] = now
    state['last_run_id'] = run_id
    state['latest_rule_version'] = rules['version']
    state['latest_rule_sha256'] = digest(rules)
    state['latest_source_contract_sha256'] = digest(client.contract.to_dict())
    state['last_run_event'] = run_event
    if run_event == 'schedule':
        state['last_automatic_run_at'] = now
    _expire_paper_windows(state, now)
    if utc(now)[:10] > rules['valid_until']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'PUBLIC_RULE_REVIEW_EXPIRED'
        state['learning_status'] = 'PAUSED'
        state['last_run_end_at'] = _effective_now(client, now)
        return dict(status='PAUSED', error='PUBLIC_RULE_REVIEW_EXPIRED', run_id=run_id)
    if state['kill_switch']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = 'KILL_SWITCH'
        state['learning_status'] = 'PAUSED'
        state['last_run_end_at'] = _effective_now(client, now)
        return dict(status='PAUSED', run_id=run_id)
    result = collect(state, client, now)
    if not result['ok']:
        state['learning_status'] = 'PAUSED'
        state['learning_reason'] = dict(status='PAUSED', reason='SOURCE_UNAVAILABLE')
        state['last_run_end_at'] = _effective_now(client, now)
        return dict(status=state['source_status'], error=result['error'], run_id=run_id)
    if run_event == 'schedule':
        state['last_automatic_source_success_at'] = state['last_source_success_at']
    data = result['data']
    active_model = next((m for m in state['models'] if m['id'] == state['active_model_id'] and
                         m['status'] == 'RESEARCH_ACTIVE'), None)
    if active_model is not None and not learning.compatible_model(active_model):
        learning.rollback(state)
        state['learning_reason'] = dict(status='RULES_BASELINE',reason='MODEL_SCHEMA_OR_CHECKSUM_FALLBACK')
        active_model = None
    preliminary_at = _effective_now(client, now)
    feature_history = learning._first_observations(state['observations'], preliminary_at)
    rows = screen(state['mapping'], data['latest'], data['5m'], data['1h'], rules, preliminary_at,
                  state.get('daily_volume'), active_model, feature_history)
    if state.get('chart_budget_day') != preliminary_at//86400:
        state['chart_budget_day'], state['chart_budget_used'] = preliminary_at//86400, 0
    # At most sixteen historical chart requests per UTC day. These downloads
    # have reconstructed availability and never become prospective ML labels.
    for row in [r for r in rows if not r['flags']][:4]:
        if state['chart_budget_used'] >= 16:
            break
        key = str(row['item_id'])
        cached = state['history_cache'].get(key)
        if cached and preliminary_at-cached['received_at'] < 86400:
            continue
        state['chart_budget_used'] += 1
        try:
            raw, meta = client.fetch('timeseries', {'id': row['item_id'], 'lookback': '7d'})
            series = parse_timeseries(raw, meta['received_at'], row['item_id'])
            state['history_cache'][key] = dict(**series, received_at=meta['received_at'], source_hash=meta['sha256'])
            state['capture_metadata'].append(meta)
        except ProviderDeferred as exc:
            state['history_errors'] = state.get('history_errors', {})
            state['history_errors'][key] = str(exc)
            state['circuit_open_until'] = max(state.get('circuit_open_until',0),exc.retry_at)
            state['source_status'] = 'DATA_GAP'
            state['source_error'] = str(exc)
            break  # Provider requested a pause; no further route may be fetched.
        except ValidationError as exc:
            state.setdefault('history_errors', {})[key] = str(exc)
            if str(exc) in ('HTTP_401', 'HTTP_403'):
                state['public_pause'] = True
                state['source_status'] = 'PAUSED'
                state['source_error'] = str(exc)
                break  # An access denial applies to the provider, not one chart.
            if str(exc).startswith('TRANSIENT_NETWORK_FAILURE'):
                state['circuit_open_until'] = max(state.get('circuit_open_until', 0),
                                                  _effective_now(client, preliminary_at) + 300)
                state['source_status'] = 'DATA_GAP'
                state['source_error'] = str(exc)
                break  # Do not repeat exhausted network retries on other IDs.
    decision_at = _effective_now(client, preliminary_at)
    if utc(decision_at)[:10] > rules['valid_until'] or client.contract.blockers(decision_at):
        state['source_status'] = 'PAUSED'
        state['source_error'] = ('PUBLIC_RULE_REVIEW_EXPIRED' if utc(decision_at)[:10] > rules['valid_until']
                                 else 'SOURCE_CONTRACT_NOT_REVIEWED')
    rows = screen(state['mapping'], data['latest'], data['5m'], data['1h'], rules, decision_at,
                  state.get('daily_volume'), active_model, feature_history)
    state['history_cache'] = {k:v for k,v in state['history_cache'].items() if decision_at-v['received_at'] < 8*86400}
    cohort_index = data['5m']['start'] // 7200
    if state.get('research_cohort_index') != cohort_index:
        state['research_cohort_index'] = cohort_index
        state['research_cohort_ids'] = sample_research_ids(rows,data['5m']['start'])[0][5:]
    selected_ids, ml_ids = sample_research_ids(
        rows,data['5m']['start'],state.get('research_cohort_ids',[]))
    selected_ids += pending_pick_observation_ids(state['picks'],decision_at)
    selected_ids += [p['item_id'] for d in state.get('shadow_decisions',[])
                     if d['issued_at']+learning.HORIZON_SECONDS+300>=decision_at
                     for p in d['baseline']+d['candidate']]
    for key, five in data.items():
        if key == '5m' or key.startswith('5m:'):
            meta = next((c for c in result['captures'] if c['kind'] == '5m' and
                         (key == '5m' and '?' not in c['url'] or key != '5m' and str(five['start']) in c['url'])), None)
            if meta:
                _append_observations(state, five, meta['sha256'], meta['received_at'], set(selected_ids),
                                     'CAPTURED_5M' if key == '5m' else 'RETROSPECTIVE_BACKFILL',
                                     meta.get('version'))
    state['observations'] = [o for o in state['observations'] if o['bucket_start'] >= decision_at - OBS_RETENTION]
    grade_picks(state, state['observations'], decision_at)
    state['predictions'] = [p for p in state['predictions'] if p['issued_at'] >= decision_at - OBS_RETENTION]
    state['shadow_decisions'] = [d for d in state['shadow_decisions'] if d['issued_at'] >= decision_at - OBS_RETENTION]
    learning.daily_readiness(state, decision_at)
    first_for_features = learning._first_observations(state['observations'], decision_at)
    current_for_ml = [dict(item_id=item_id, bucket_start=data['5m']['start'],
                           previous=first_for_features.get((item_id, data['5m']['start']-300)),
                           **data['5m']['items'][str(item_id)])
                      for item_id in ml_ids if str(item_id) in data['5m']['items']]
    learning.record_predictions(state, current_for_ml, decision_at)
    if record_shadow_decision(state, rows, data['5m'], decision_at):
        newest=state['shadow_decisions'][-1]
        new_ids={p['item_id'] for p in newest['baseline']+newest['candidate']}
        meta=next((c for c in result['captures'] if c['kind']=='5m' and '?' not in c['url']),None)
        if meta:
            _append_observations(state,data['5m'],meta['sha256'],meta['received_at'],new_ids,
                                 'CAPTURED_5M',meta.get('version'))
    issued = issue_picks(state, rows, decision_at, result['captures'])
    compact_history(state, archive_dir, decision_at)
    if state['public_pause']:
        state['source_status'] = 'PAUSED'
        state['source_error'] = state['source_error'] or 'PUBLIC_PAUSE'
        state['learning_status'] = 'PAUSED'
    state['latest_screen'] = rows
    state['latest_screen_at'] = decision_at
    state['latest_capture_hashes'] = [c['sha256'] for c in result['captures']]
    state['last_run_end_at'] = _effective_now(client, decision_at)
    raw_due = state.get('last_raw_evidence_day') != decision_at//86400
    if raw_due:
        state['last_raw_evidence_day'] = decision_at//86400
    return dict(status=state['source_status'], run_id=run_id, screened=len(rows),
                eligible=sum(not r['flags'] for r in rows), issued=len(issued),
                learning_status=state['learning_status'], source_calls=client.calls,
                source_raw_bytes=client.raw_bytes, raw_evidence_due=raw_due)


def mark_published(state, pick_ids, deployed_at, deployment_id, url,
                   artifact_id=None, export_sha256=None, run_event='unknown'):
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
    for decision in state.get('shadow_decisions', []):
        if decision.get('run_id') == deployment_id and decision.get('public_available_at') is None:
            decision['public_available_at'] = deployed_at
    receipt = dict(deployment_id=deployment_id, deployed_at=deployed_at,
        url=url, pick_ids=sorted(pick_ids), acknowledged=changed,
        artifact_id=artifact_id, export_sha256=export_sha256, run_event=run_event)
    state['publication_receipts'].append(receipt)
    state['publication_receipts'] = state['publication_receipts'][-1000:]
    if run_event == 'schedule':
        state['last_automatic_publication_at'] = deployed_at
    if (state.get('pending_publication') or {}).get('deployment_id') == deployment_id:
        state['pending_publication'] = None
    return changed


def recover_pending_publication(state, jobs, checked_at, url):
    """Acknowledge a prior Pages deploy using its verified completion clock."""
    pending = state.get('pending_publication')
    if not pending:
        return dict(status='NO_PENDING_DEPLOYMENT')
    deploys = [job for job in jobs if job.get('name') == 'deploy']
    if len(deploys) != 1 or deploys[0].get('status') != 'completed':
        raise ValidationError('Previous Pages deployment state is not yet verifiable.')
    if deploys[0].get('conclusion') != 'success':
        state['pending_publication'] = None
        return dict(status='PREVIOUS_DEPLOYMENT_FAILED', deployment_id=pending['deployment_id'])
    deploy_completed_at = verified_deploy_completed_at(jobs, checked_at)
    marked = mark_published(state, set(pending['pick_ids']), deploy_completed_at,
        pending['deployment_id'], url, pending['artifact_id'],
        pending['export_sha256'], pending['run_event'])
    state['publication_receipts'][-1]['recovered_at_next_run'] = checked_at
    state['publication_receipts'][-1]['availability_clock'] = 'VERIFIED_DEPLOY_JOB_COMPLETION'
    return dict(status='RECOVERED_PRIOR_DEPLOYMENT', published=marked,
                deployment_id=pending['deployment_id'])


def verified_deploy_completed_at(jobs, checked_at):
    deploys = [job for job in jobs if job.get('name') == 'deploy']
    if len(deploys) != 1 or deploys[0].get('status') != 'completed' or deploys[0].get('conclusion') != 'success':
        raise ValidationError('Successful Pages deploy job is not verifiable.')
    raw = deploys[0].get('completed_at')
    try:
        completed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if completed.tzinfo is None:
            raise ValueError('timezone absent')
        timestamp = int(completed.timestamp())
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValidationError('Pages deploy completion timestamp is invalid.') from None
    if timestamp <= 0 or timestamp > checked_at + 1:
        raise ValidationError('Pages deploy completion timestamp is outside the verified clock.')
    return timestamp


def github_attempt_jobs(deployment_id):
    token = os.environ.get('GITHUB_TOKEN')
    if os.environ.get('GITHUB_REPOSITORY', '').lower() != 'nicknicky19/osrs-flipper' or not token:
        raise ValidationError('Prior deployment recovery requires this repository and its Actions read token.')
    run_id, attempt = deployment_id.split('-')
    url = (f'https://api.github.com/repos/NickNicky19/osrs-flipper/actions/runs/'
           f'{run_id}/attempts/{attempt}/jobs?per_page=100')
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'osrs-flipper-public-recovery/1'})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read(1_000_001)
    except urllib.error.HTTPError as error:
        raise ValidationError(f'Prior deployment verification failed with HTTP {error.code}.') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValidationError('Prior deployment verification is temporarily unavailable.') from None
    if len(body)>1_000_000:
        raise ValidationError('Prior deployment verification exceeded bounded response size.')
    decoded = strict_json(body)
    if not isinstance(decoded,dict) or not isinstance(decoded.get('jobs'),list) or decoded.get('total_count',101)>100:
        raise ValidationError('Prior deployment job response is incomplete.')
    return decoded['jobs']


PUBLIC_KEYS = {'schema', 'generated_at', 'artifact_id', 'artifact_run_id',
               'source_status', 'source_error', 'source_success_at', 'run_at', 'run_end_at',
               'known_data_gap',
               'last_automatic_run_at', 'last_automatic_source_success_at', 'last_automatic_publication_at',
               'daily_volume_as_of', 'recent_route_captures', 'source_route_counts',
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
    screen_keys = ('item_id', 'name', 'members', 'observed_instant_sell_low', 'observed_instant_buy_high',
        'low_time', 'high_time', 'five_low_units', 'five_high_units', 'hour_low_units',
        'hour_high_units', 'day_low_units', 'day_high_units', 'public_limit', 'limit_group_status',
        'quantity_assumption', 'required_cash',
        'conditional_after_tax_gp', 'tax_exempt', 'rule_status', 'rule_version',
        'legacy_baseline_supported', 'preference_flags', 'status', 'flags')
    screener = [{k: r.get(k) for k in screen_keys} for r in state.get('latest_screen', [])] if fresh else []
    graphs = {}
    first = learning._first_observations(state['observations'], now)
    graph_ids = list(dict.fromkeys([p['item_id'] for p in top] +
        [int(k) for k, _ in sorted(state['history_cache'].items(),
                                  key=lambda pair: pair[1]['received_at'], reverse=True)] +
        [item for item, _ in first]))[:MAX_GRAPH_ITEMS]
    for item_id in graph_ids:
        captured = [dict(bucket_start=o['bucket_start'], low_avg=o['low_avg'], high_avg=o['high_avg'],
                         low_units=o['low_units'], high_units=o['high_units'],
                         received_at=o['received_at'], provenance=o['source'])
                    for (item, _), o in first.items() if item == item_id]
        cache = state['history_cache'].get(str(item_id))
        older = [dict(p, provenance='RETROSPECTIVE_DOWNLOAD', received_at=cache['received_at'])
                 for p in cache['points'][-288:]] if cache else []
        points = sorted(older + captured, key=lambda x: (x['bucket_start'], x['received_at']))[-576:]
        graphs[str(item_id)] = dict(stats=graph_stats(older if older else captured),
            stats_scope='RETROSPECTIVE_DOWNLOAD' if older else 'FIRST_HAND_CAPTURE',
            recent_stats=graph_stats(captured), historical_stats=graph_stats(older),
            points=points, captured_points=sorted(captured,key=lambda x:x['bucket_start'])[-288:],
            historical_points=older,
            resolution_seconds=cache['timestep'] if cache else 300,
            availability='MIXED_PROVENANCE' if older and captured else
                         ('RETROSPECTIVE_DOWNLOAD' if older else 'FIRST_HAND_CAPTURE'),
            received_at=cache['received_at'] if cache else None,
            captured_recent_points=len(captured))
    rejections = {}
    for row in screener:
        for flag in row['flags']:
            rejections[flag] = rejections.get(flag, 0) + 1
    rule_supported = sum(r['rule_status'] in ('ORDINARY', 'EXEMPT') and
                         type(r['public_limit']) is int for r in screener)
    fresh_two_sided = sum(r['observed_instant_sell_low'] is not None and
                          r['observed_instant_buy_high'] is not None and
                          not {'MISSING_SIDE','STALE_SIDE','PUBLICATION_WINDOW_TOO_SHORT'} & set(r['flags'])
                          for r in screener)
    activity_supported = sum(not {'TWO_SIDED_ACTIVITY_UNKNOWN','STALE_5M_BUCKET','STALE_1H_BUCKET'} &
                              set(r['flags']) for r in screener)
    positive_net = sum(r['conditional_after_tax_gp'] is not None and
                       r['conditional_after_tax_gp'] > 0 for r in screener)
    recent_route_captures={}
    for capture in reversed(state['capture_metadata']):
        kind=capture.get('kind')
        if kind in ('mapping','latest','5m','1h','24h') and kind not in recent_route_captures and '?' not in capture.get('url',''):
            recent_route_captures[kind]=dict(accepted=capture.get('accepted_items'),
                quarantined=capture.get('quarantined_items'),received_at=capture.get('received_at'))
    model = next((m for m in reversed(state['models']) if m['status'] in ('SHADOW', 'RESEARCH_ACTIVE')), None)
    learning_view = dict(status=state['learning_status'], active_model_id=state['active_model_id'],
        candidate_model_id=model['id'] if model else None, model_version=model['version'] if model else None,
        model_code_commit=model.get('code_commit') if model else None,
        model_rule_version=model.get('rule_version') if model else None,
        model_source_route_versions=model.get('source_route_versions') if model else None,
        feature_schema=learning.FEATURE_SCHEMA,target_schema=learning.TARGET_SCHEMA,
        readiness=learning.readiness_report(state,now),
        learning_reason=state.get('learning_reason'),
        last_training_at=state['last_training_at'], last_evaluation_at=state['last_evaluation_at'],
        last_training_duration_ms=state.get('last_training_duration_ms'),
        latest_data_cutoff=state['latest_data_cutoff'],
        samples=model['samples'] if model else None, metrics=model['metrics'] if model else None,
        shadow=state.get('shadow_metrics', {}).get(model['id']) if model else None,
        economic_shadow_comparison=state.get('economic_shadow_comparison'),
        promotion_gate=state.get('promotion_gate'),
        evidence_scope='Public interval averages and reported units only. No personal execution labels or verified trading edge.')
    body = dict(schema='osrs-public-site-1', generated_at=now,
        artifact_id=None, artifact_run_id=state.get('last_run_id'),
        source_status=state['source_status'] if state['source_status'] != 'CAPTURED_WITH_REVIEW'
                      else ('CAPTURED_WITH_REVIEW' if fresh else 'STALE_OR_UNAVAILABLE'),
        source_error=state['source_error'], source_success_at=state['last_source_success_at'],
        known_data_gap=state.get('data_gap'),
        run_at=state['last_run_at'], run_end_at=state.get('last_run_end_at'),
        last_automatic_run_at=state.get('last_automatic_run_at'),
        last_automatic_source_success_at=state.get('last_automatic_source_success_at'),
        last_automatic_publication_at=state.get('last_automatic_publication_at'),
        daily_volume_as_of=state.get('last_24h_at'),recent_route_captures=recent_route_captures,
        source_route_counts=state.get('route_counts') or {},
        quote_valid_until=min((p['expires_at'] for p in picks), default=None),
        bankroll=BANKROLL, live_pilot_ceiling=LIVE_PILOT_CEILING,
        cash_available=state['paper_cash'],
        cash_reserved=state['paper_reserved'], top_picks=top,
        screener=screener, graphs=graphs, history=histories,
        outcomes=state['outcomes'][-120:], learning=learning_view,
        coverage=dict(mapped=len(state.get('mapping') or {}), screened=len(screener),
                      rule_supported=rule_supported, fresh_two_sided=fresh_two_sided,
                      activity_supported=activity_supported, positive_net=positive_net,
                      eligible=sum(r['status']=='ELIGIBLE_RESEARCH' for r in screener),
                      legacy_baseline_candidates=sum(r['status']=='ELIGIBLE_RESEARCH' and
                                                     r['legacy_baseline_supported'] for r in screener),
                      rejection_flags=rejections, flag_counts_overlap=True,
                      exported_candidate_pool_rows=len(screener), graph_items=len(graphs),
                      captured_observations=len(state['observations']), archived_counts=state['archived_counts']),
        versions=dict(pipeline=VERSION, policy=POLICY, research_policy=RESEARCH_POLICY,
                      rules=state.get('latest_rule_version') or 'public-rules-1'),
        publication=state['publication_receipts'][-1] if state['publication_receipts'] else None,
        performance=dict(conditional_active_portfolio_gp=sum(p['conditional_after_tax_gp'] for p in picks),
            simulated_realized_gp=None, simulated_inventory_at_cost=None,
            uncertain_reference_equity=None,
            forward_price_paths_graded=sum(o['status']=='PRICE_PATH_OBSERVED' for o in state['outcomes']),
            forward_negative_reference_marks=sum(o.get('reference_mark_gp') is not None and o['reference_mark_gp'] < 0
                                                 for o in state['outcomes']),
            historical_simulation_status='NOT_SIMULATED',
            actual_user_recorded_status='PRIVATE_NOT_CONNECTED',
            note='No fill model is activated. Cash reservations are hypothetical order windows; no purchase or sale is credited.'),
        methodology='All top-pick GP is conditional on full assumed same-item buy and later sell. Market volume is not queue depth; no fills or real profit are inferred.')
    if set(body) != PUBLIC_KEYS:
        raise AssertionError('Public export allowlist drift.')
    body['artifact_id'] = digest({key: value for key, value in body.items() if key != 'artifact_id'})[:32]
    return body


def write_export(path, body):
    raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_EXPORT_BYTES:
        raise ValidationError('Public export exceeds bounded size; preserve state and pause publication.')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('init', 'cycle', 'publish', 'recover', 'export', 'rollback'))
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
    parser.add_argument('--artifact-id')
    parser.add_argument('--export-sha256')
    parser.add_argument('--run-event', choices=('push', 'workflow_dispatch', 'schedule', 'manual'), default='manual')
    parser.add_argument('--receipt-output', type=Path)
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
        result = cycle(state, client, load_rules(args.rules), now, args.run_id,
                       args.archive_dir, run_event=args.run_event)
    elif args.operation == 'publish':
        if not args.deployment_id or not args.deployment_url:
            raise ValidationError('Deployment receipt required.')
        deployed_at = verified_deploy_completed_at(github_attempt_jobs(args.deployment_id), now)
        count = mark_published(state, set(filter(None, args.pick_ids.split(','))), deployed_at,
                               args.deployment_id, args.deployment_url,
                               args.artifact_id, args.export_sha256, args.run_event)
        result = dict(published=count)
        state['publication_receipts'][-1]['acknowledged_at'] = now
        state['publication_receipts'][-1]['availability_clock'] = 'VERIFIED_DEPLOY_JOB_COMPLETION'
        if args.receipt_output:
            receipt=next(r for r in state['publication_receipts']
                         if r['deployment_id'] == args.deployment_id)
            args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_output.write_text(json.dumps(receipt,sort_keys=True,separators=(',', ':')),
                                           encoding='utf-8')
    elif args.operation == 'rollback':
        learning.rollback(state)
        result = dict(status='RULES_BASELINE')
    elif args.operation == 'recover':
        if not args.deployment_url:
            raise ValidationError('Expected Pages URL required for recovery.')
        pending=state.get('pending_publication')
        result=recover_pending_publication(state,
            github_attempt_jobs(pending['deployment_id']) if pending else [],
            int(time.time()), args.deployment_url)
        if result['status']=='RECOVERED_PRIOR_DEPLOYMENT' and args.receipt_output:
            args.receipt_output.parent.mkdir(parents=True,exist_ok=True)
            args.receipt_output.write_text(json.dumps(state['publication_receipts'][-1],sort_keys=True,
                separators=(',', ':')),encoding='utf-8')
    if args.output:
        exported = public_export(state, int(time.time()))
        result['artifact_id'] = exported['artifact_id']
        result['export_sha256'] = write_export(args.output, exported)
        if args.operation == 'cycle' and args.run_event != 'manual' and re.fullmatch(r'\d+-\d+',args.run_id or ''):
            state['pending_publication'] = dict(deployment_id=args.run_id,
                artifact_id=result['artifact_id'],export_sha256=result['export_sha256'],
                pick_ids=list(state['active_pick_ids']),run_event=args.run_event)
    if args.operation != 'export':
        result['state_sha256'] = save_state(args.state, state)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
