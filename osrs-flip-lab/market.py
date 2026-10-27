"""Timestamped market capture and point-in-time views. Standard library only.

No order book, full-market-volume, fill probability, or trading-edge claim.
API semantics/permission are an explicit, expiring LOCAL review, not inferred
from a successful request. The synthetic and public stores cannot be mixed.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
import gzip
import hashlib
import json
import sqlite3
import time
from typing import Any
from core import ValidationError, integer

VERSION = 'market-contract-0.3.0'
MAX_RAW_BYTES = 12_000_000
KINDS = {'mapping', 'latest', '5m', '1h', '24h'}
INTERVAL_SECONDS = {'5m': 300, '1h': 3600, '24h': 86400}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(raw: bytes | str) -> Any:
    def pairs(items):
        out = {}
        for key, val in items:
            if key in out:
                raise ValidationError('Duplicate JSON key: ' + key)
            out[key] = val
        return out
    def bad_constant(value):
        raise ValidationError('Non-finite JSON value: ' + value)
    try:
        if len(raw) > MAX_RAW_BYTES:
            raise ValidationError('Response exceeds the configured size limit.')
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError('Invalid JSON: ' + str(exc)) from exc


def obj(value, label):
    if not isinstance(value, dict):
        raise ValidationError(label + ' must be an object.')
    return value


def item_id(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and str(int(value)) == value:
        value = int(value)
    return integer(value, 'Item ID', 1)


@dataclass(frozen=True)
class SourceContract:
    api_version: str = 'v1'
    bucket_timestamp: str = 'start'
    reviewed: bool = False
    reviewed_at: int = 0
    expires_at: int = 0
    permission_reference: str = ''
    semantics_reference: str = ''
    user_agent: str = ''
    route_versions: dict | None = None

    @classmethod
    def from_dict(cls, value):
        value = obj(value, 'Source review')
        if set(value) - set(cls.__dataclass_fields__):
            raise ValidationError('Unknown source-review fields.')
        result = cls(**value)
        if result.api_version not in ('v1', 'v2') or result.bucket_timestamp not in ('start', 'end'):
            raise ValidationError('Explicit API version and start/end timestamp convention required.')
        if result.route_versions is not None:
            if not isinstance(result.route_versions, dict) or not set(result.route_versions) <= KINDS | {'timeseries'}:
                raise ValidationError('Invalid route-version registry.')
            if any(v not in ('v1', 'v2') for v in result.route_versions.values()):
                raise ValidationError('Invalid route version.')
        if type(result.reviewed) is not bool:
            raise ValidationError('Source review must be a boolean.')
        integer(result.reviewed_at, 'Review timestamp')
        integer(result.expires_at, 'Review expiry')
        for key in ('permission_reference', 'semantics_reference', 'user_agent'):
            if not isinstance(getattr(result, key), str) or len(getattr(result, key)) > 1000:
                raise ValidationError('Invalid ' + key)
        if any(c in result.user_agent for c in '\r\n'):
            raise ValidationError('Invalid User-Agent.')
        return result

    def blockers(self, now):
        if not self.reviewed:
            return ['SOURCE_REVIEW_NOT_RECORDED']
        if not (0 < self.reviewed_at <= now < self.expires_at):
            return ['SOURCE_REVIEW_EXPIRED_OR_FUTURE']
        if not self.permission_reference.strip() or not self.semantics_reference.strip() or not self.user_agent.strip():
            return ['SOURCE_REVIEW_INCOMPLETE']
        return []

    def to_dict(self):
        result = asdict(self)
        if result['route_versions'] is None:
            del result['route_versions']
        return result

    def version_for(self, kind):
        if kind not in KINDS | {'timeseries'}:
            raise ValidationError('Unsupported endpoint.')
        return (self.route_versions or {}).get(kind, self.api_version)


def parse_payload(kind: str, raw: bytes, received_at: int, contract: SourceContract) -> dict:
    """Strict known-field contract. Unknown extra fields are ignored, not invented."""
    integer(received_at, 'Receipt timestamp', 1)
    if kind not in KINDS:
        raise ValidationError('Unsupported endpoint.')
    root = strict_json(raw)
    result = {'items': {}}
    if kind == 'mapping':
        if not isinstance(root, list) or not root:
            raise ValidationError('Mapping must be a nonempty array.')
        for entry in root:
            entry = obj(entry, 'Mapping entry')
            ident = str(item_id(entry.get('id')))
            name = entry.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
                raise ValidationError('Invalid item name.')
            if type(entry.get('members')) is not bool:
                raise ValidationError('Membership must be explicit.')
            limit = entry.get('limit')
            if limit is not None:
                integer(limit, 'Public item limit', 1)
            if ident in result['items']:
                raise ValidationError('Duplicate mapping item.')
            result['items'][ident] = dict(name=name, members=entry['members'], public_limit=limit)
        return result
    root = obj(root, 'Response')
    data = obj(root.get('data'), 'Response data')
    if kind in INTERVAL_SECONDS:
        timestamp = integer(root.get('timestamp'), 'Bucket timestamp', 1)
        seconds = INTERVAL_SECONDS[kind]
        if timestamp % seconds:
            raise ValidationError('Interval bucket is not aligned.')
        start = timestamp if contract.bucket_timestamp == 'start' else timestamp - seconds
        if start < 0 or start + seconds > received_at:
            raise ValidationError('Incomplete or future interval rejected.')
        result.update(start=start, end=start + seconds, seconds=seconds)
    for key, value in data.items():
        ident = str(item_id(key))
        entry = obj(value, 'Item observation')
        row = {}
        if kind == 'latest':
            for side in ('low', 'high'):
                price, stamp = entry.get(side), entry.get(side + 'Time')
                if (price is None) != (stamp is None):
                    raise ValidationError('Price/time nullability differs.')
                if price is not None:
                    integer(price, side + ' price', 1)
                    integer(stamp, side + ' timestamp', 1)
                    if stamp > received_at:
                        raise ValidationError('Future quote timestamp rejected.')
                row[side], row[side + '_time'] = price, stamp
        else:
            for side in ('low', 'high'):
                count = entry.get(side + 'PriceVolume')
                price = entry.get('avg' + side.title() + 'Price')
                # Null units remain UNKNOWN. They are never silently changed to zero.
                if count is not None:
                    integer(count, side + ' units')
                if price is not None:
                    if type(price) not in (float, int) or not 0 < price <= 9_000_000_000_000_000:
                        raise ValidationError('Invalid average price.')
                if count and price is None:
                    raise ValidationError('Nonzero volume without an average price.')
                row[side + '_units'], row[side + '_avg'] = count, price
        result['items'][ident] = row
    return result


class MarketStore:
    """Separate immutable captures and fetch attempts. Duplicate bars never add volume.

    Repeat receipts are stored as attempts, not new observations. Changed response
    bodies are revisions; an as-of query sees only revisions received by that time.
    Hashes detect accidental changes, not a hostile editor controlling all files.
    """
    def __init__(self, path: Path, namespace='public'):
        if namespace not in ('public', 'fixture'):
            raise ValidationError('Unknown market namespace.')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY CHECK(id=1),namespace TEXT);
              CREATE TABLE IF NOT EXISTS captures(
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, received_at INTEGER NOT NULL,
                digest TEXT NOT NULL, contract TEXT NOT NULL, contract_hash TEXT NOT NULL,
                bucket_start INTEGER, valid INTEGER NOT NULL, error TEXT, raw_gzip BLOB NOT NULL,
                UNIQUE(kind,digest,contract_hash,valid));
              CREATE TABLE IF NOT EXISTS attempts(
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, received_at INTEGER NOT NULL,
                capture_id INTEGER, ok INTEGER NOT NULL, error TEXT);
              CREATE INDEX IF NOT EXISTS captures_time ON captures(kind,received_at);
              CREATE TABLE IF NOT EXISTS decisions(
                id TEXT PRIMARY KEY,digest TEXT NOT NULL,created_at INTEGER NOT NULL,body TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO identity VALUES(1,?)', (namespace,))
            if db.execute('SELECT namespace FROM identity').fetchone()[0] != namespace:
                raise ValidationError('Synthetic and public market stores must remain separate.')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, kind, raw: bytes, received_at: int, contract: SourceContract):
        if kind not in KINDS or not isinstance(raw, bytes) or len(raw) > MAX_RAW_BYTES:
            raise ValidationError('Unsupported or oversized response.')
        integer(received_at, 'Receipt timestamp', 1)
        parsed, error = None, None
        try:
            parsed = parse_payload(kind, raw, received_at, contract)
        except ValidationError as exc:
            error = str(exc)
        digest = hashlib.sha256(raw).hexdigest()
        review = contract.to_dict()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT OR IGNORE INTO captures
              (kind,received_at,digest,contract,contract_hash,bucket_start,valid,error,raw_gzip)
              VALUES(?,?,?,?,?,?,?,?,?)''', (kind, received_at, digest, canonical(review), sha(review),
                 parsed.get('start') if parsed else None, int(error is None), error, gzip.compress(raw, mtime=0)))
            rec = db.execute('SELECT id,valid,error FROM captures WHERE kind=? AND digest=? AND contract_hash=? AND valid=?',
                             (kind,digest,sha(review),int(error is None))).fetchone()
            # Revalidation can create a NEW accepted capture, never rewrite or backdate a quarantine.
            ok = bool(rec['valid']) and error is None
            db.execute('INSERT INTO attempts(kind,received_at,capture_id,ok,error) VALUES(?,?,?,?,?)',
                       (kind,received_at,rec['id'],int(ok),error or rec['error']))
        return {'capture_id': rec['id'], 'digest': digest, 'ok': ok, 'error': error or rec['error']}

    def failure(self, kind, error, received_at=None):
        if kind not in KINDS:
            raise ValidationError('Unknown endpoint.')
        stamp = int(time.time()) if received_at is None else integer(received_at, 'Receipt timestamp', 1)
        with self.connect() as db:
            db.execute('INSERT INTO attempts(kind,received_at,ok,error) VALUES(?,?,0,?)',
                       (kind,stamp,str(error)[:1000]))

    def read_capture(self, row):
        try:
            compressed = row['raw_gzip']
            # Reject decompression bombs even for a corrupted local database.
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as f:
                raw = f.read(MAX_RAW_BYTES + 1)
            if len(raw) > MAX_RAW_BYTES or hashlib.sha256(raw).hexdigest() != row['digest']:
                raise ValidationError('Stored market response failed integrity verification.')
            review = strict_json(row['contract'])
            if sha(review) != row['contract_hash']:
                raise ValidationError('Stored source review failed integrity verification.')
            contract = SourceContract.from_dict(review)
            data = parse_payload(row['kind'], raw, row['received_at'], contract)
            if data.get('start') != row['bucket_start']:
                raise ValidationError('Stored bucket index does not match original data.')
            return dict(id=row['id'], digest=row['digest'], received_at=row['received_at'],
                        confirmed_at=row['confirmed_at'] if 'confirmed_at' in row.keys() else row['received_at'],
                        contract=contract.to_dict(), data=data)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError('Unreadable market capture; preserve the store for review.') from exc

    def snapshot(self, as_of=None, window_seconds=3600):
        now = int(time.time()) if as_of is None else integer(as_of, 'As-of timestamp', 1)
        if window_seconds not in (900, 3600, 14400):
            raise ValidationError('Supported activity windows are 15 minutes, 1 hour and 4 hours.')
        end = now - now % 300
        start = end - window_seconds
        with self.connect() as db:
            db.execute('BEGIN')
            attempts = [dict(x) for x in db.execute('''SELECT * FROM (SELECT *,
                ROW_NUMBER() OVER(PARTITION BY kind ORDER BY received_at DESC,id DESC) rn
                FROM attempts WHERE received_at<=?) WHERE rn=1''',(now,))]
            latest = {}
            for kind in ('mapping','latest'):
                row = db.execute('''SELECT c.*,a.received_at AS confirmed_at FROM attempts a JOIN captures c ON c.id=a.capture_id
                    WHERE c.kind=? AND c.valid=1 AND a.ok=1 AND a.received_at<=?
                    ORDER BY a.received_at DESC,a.id DESC LIMIT 1''',(kind,now)).fetchone()
                latest[kind] = self.read_capture(row) if row else None
            bars = []
            # Pick the complete response revision, not a per-item mix of old and new revisions.
            rows = db.execute('''SELECT * FROM (
                 SELECT c.*,a.received_at AS confirmed_at,ROW_NUMBER() OVER(PARTITION BY c.bucket_start ORDER BY a.received_at DESC,a.id DESC) rn
                 FROM attempts a JOIN captures c ON c.id=a.capture_id WHERE c.kind='5m' AND c.valid=1 AND a.ok=1 AND a.received_at<=?
                 AND c.bucket_start>=? AND c.bucket_start<?) WHERE rn=1 ORDER BY bucket_start''',(now,start,end))
            for row in rows:
                bars.append(self.read_capture(row))
        return dict(namespace=self.namespace,as_of=now,window_start=start,window_end=end,
                    expected_intervals=window_seconds//300,mapping=latest['mapping'],latest=latest['latest'],
                    bars=bars,attempts=attempts)

    def freeze(self, decision_id: str, snapshot_body: dict, created_at=None):
        if not isinstance(decision_id,str) or not 1 <= len(decision_id) <= 80:
            raise ValidationError('A stable decision ID is required.')
        if snapshot_body.get('namespace') != self.namespace:
            raise ValidationError('Decision namespace mismatch.')
        body = canonical(snapshot_body)
        digest = hashlib.sha256(body.encode()).hexdigest()
        stamp = int(time.time()) if created_at is None else integer(created_at,'Decision timestamp',1)
        with self.connect() as db:
            prior = db.execute('SELECT digest FROM decisions WHERE id=?',(decision_id,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ValidationError('Decision ID already refers to a different snapshot.')
            else:
                db.execute('INSERT INTO decisions VALUES(?,?,?,?)',(decision_id,digest,stamp,body))
        return dict(id=decision_id,digest=digest)

    def decisions(self):
        with self.connect() as db:
            result=[]
            for row in db.execute('SELECT * FROM decisions ORDER BY created_at,id'):
                if hashlib.sha256(row['body'].encode()).hexdigest() != row['digest']:
                    raise ValidationError('Decision receipt integrity failed.')
                result.append(dict(id=row['id'],digest=row['digest'],created_at=row['created_at'],
                                   receipt=strict_json(row['body'])))
            return result

    def export(self):
        """Logical export retains compressed source bodies as hexadecimal, not secrets."""
        with self.connect() as db:
            db.execute('BEGIN')
            caps=[]
            for r in db.execute('SELECT * FROM captures ORDER BY id'):
                item=dict(r);item['raw_gzip']=item['raw_gzip'].hex();caps.append(item)
            attempts=[dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY id')]
            decisions=[dict(r) for r in db.execute('SELECT * FROM decisions ORDER BY created_at,id')]
        return dict(schema='osrs-market-export-0.3',namespace=self.namespace,captures=caps,
                    attempts=attempts,decisions=decisions,
                    note='Contains market source bodies and saved research decisions. No game access.')


def aggregate_item(snapshot, ident):
    """Known coverage is independent for each side; never extrapolate absent buckets."""
    out={'expected_intervals':snapshot['expected_intervals'],'observed_intervals':0,
         'low_units':0,'high_units':0,'low_intervals':0,'high_intervals':0,
         'low_weighted_price':None,'high_weighted_price':None,'pairs':[]}
    weights={'low':0.0,'high':0.0}
    for cap in snapshot['bars']:
        row=cap['data']['items'].get(str(ident))
        if row is None:
            continue
        out['observed_intervals']+=1
        for side in ('low','high'):
            count=row[side+'_units']
            if count is not None:
                out[side+'_intervals']+=1
                out[side+'_units']+=count
                if count:
                    weights[side]+=count*row[side+'_avg']
        if row['low_units'] and row['high_units']:
            out['pairs'].append([row['low_avg'],row['high_avg']])
    for side in ('low','high'):
        if out[side+'_units']:
            out[side+'_weighted_price']=weights[side]/out[side+'_units']
    out['coverage_bps']=min(out['low_intervals'],out['high_intervals'])*10000//out['expected_intervals']
    return out
