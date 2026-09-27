"""Portable, private Congress dataset import and indexed read access.

Only allowlisted user data is imported. Original archives remain untouched. Row
payloads are compressed but lossless; price hashes still cover original bytes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
import zlib
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

RESEARCH = {
    'overnight-research': 'Overnight disclosure portfolios',
    'overnight-capacity': 'Overnight cash and capacity',
    'capital-capacity': 'Portfolio cash diagnostics',
    'rotation-research': 'Twenty-position rotation',
    'fresh-strategy-search': '72-rule disclosure search',
    'fresh-strategy-followup': 'Disclosure search follow-up',
    'research-overview': 'Matched-stock research and methods',
}
PAGE_FIELDS = ('id', 'politicianId', 'politician', 'chamber', 'ticker', 'priceSymbol', 'asset', 'tradeDate', 'filedDate', 'owner', 'account', 'transactionType', 'amountRange', 'action', 'eligible', 'exclusion', 'source', 'filingId')
ENGINE_FIELDS = (*PAGE_FIELDS, 'assetClass', 'dependentChildId', 'firstPublicAt', 'filedAt', 'filedTimeZoneAssumption', 'sourceVerified', 'sourceSha256', 'signalStatus')


def load_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_bytes()) if path.is_file() else default


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('x', encoding='utf-8') as out:
            os.chmod(temp, 0o600)
            json.dump(value, out, separators=(',', ':'), allow_nan=False)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def stream_array(path: Path) -> Iterator[dict]:
    """Incrementally decode a JSON array; avoid holding the 257 MB archive twice."""
    decoder = json.JSONDecoder()
    with path.open(encoding='utf-8') as source:
        buffer = ''; eof = False; started = False
        while True:
            if not eof and len(buffer) < 1_048_576:
                chunk = source.read(1_048_576)
                eof = not chunk
                buffer += chunk
            buffer = buffer.lstrip()
            if not started:
                if not buffer.startswith('['):
                    raise ValueError('Disclosure archive must be a JSON array')
                buffer = buffer[1:]; started = True
            buffer = buffer.lstrip(' \r\n\t,')
            if buffer.startswith(']'):
                return
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise ValueError('Incomplete disclosure JSON array') from None
                chunk = source.read(1_048_576); eof = not chunk; buffer += chunk
                continue
            if not isinstance(value, dict) or not value.get('id'):
                raise ValueError('Every disclosure must be an object with an ID')
            yield value
            buffer = buffer[end:]


def import_archive(source: Path, target: Path, *, include_prices: bool = True, refresh: bool = False) -> dict:
    """Install one verified local archive generation without overwriting a prior one."""
    source = source.expanduser().resolve(); target = target.expanduser().resolve()
    records = source / 'public/data/records.json'
    if not records.is_file():
        raise ValueError('No normalized Congress disclosure archive at this location')
    if (target / 'manifest.json').is_file() and not refresh:
        return load_json(target / 'manifest.json')
    with records.open('rb') as archive_file:
        source_record_hash = hashlib.file_digest(archive_file, 'sha256').hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = target.with_name('.' + target.name + '-' + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    hashes = {}; counts = Counter(); people = {}; symbols = set(); latest = ''; earliest = ''
    backup = None
    try:
        with closing(sqlite3.connect(staging / 'congress.sqlite3')) as db:
            db.execute('CREATE TABLE records (id TEXT PRIMARY KEY, politician_id TEXT, chamber TEXT, owner TEXT, action TEXT, filed_date TEXT, trade_date TEXT, verified INTEGER, eligible INTEGER, search TEXT, page BLOB, engine BLOB, payload BLOB)')
            for r in stream_array(records):
                page = {k: r.get(k) for k in PAGE_FIELDS}
                engine = {k: r.get(k) for k in ENGINE_FIELDS}
                payload = json.dumps(r, separators=(',', ':')).encode()
                search = ' '.join(str(r.get(k) or '') for k in ('politician', 'ticker', 'priceSymbol', 'asset', 'account', 'filingId')).casefold()
                db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (r['id'], r.get('politicianId'), r.get('chamber'), r.get('owner'), r.get('action'), r.get('filedDate'), r.get('tradeDate'), r.get('sourceVerified') is True, bool(r.get('eligible')), search, zlib.compress(json.dumps(page, separators=(',', ':')).encode()), zlib.compress(json.dumps(engine, separators=(',', ':')).encode()), zlib.compress(payload)))
                counts[r.get('chamber') or 'unknown'] += 1
                person = people.setdefault(r['politicianId'], {'id':r['politicianId'], 'name':r['politician'], 'chamber':r['chamber'], 'records':0})
                person['records'] += 1
                if r.get('eligible') and r.get('priceSymbol'): symbols.add(r['priceSymbol'])
                if r.get('filedDate'):
                    latest = max(latest, r['filedDate']); earliest = min(earliest or r['filedDate'], r['filedDate'])
            db.execute('CREATE INDEX records_filter ON records(chamber,politician_id,filed_date DESC)')
            db.execute('CREATE INDEX records_date ON records(filed_date DESC,trade_date DESC,id DESC)')
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Imported disclosure index failed integrity verification')
        with records.open('rb') as archive_file:
            hashes['records.json'] = hashlib.file_digest(archive_file, 'sha256').hexdigest()
        if hashes['records.json'] != source_record_hash:
            raise ValueError('Source disclosure archive changed during import; try again after collection finishes')
        generation = load_json(source/'public/data/generation.json', {})
        if generation.get('recordCount') is not None and generation['recordCount'] != sum(counts.values()):
            raise ValueError('Source disclosure generation does not match its record count')
        for name in RESEARCH:
            old = source / 'public-site/data' / (name + '.json')
            if old.is_file():
                new = staging / 'research-snapshots' / old.name; new.parent.mkdir(exist_ok=True)
                shutil.copyfile(old, new); hashes[name+'.json'] = hashlib.sha256(old.read_bytes()).hexdigest()
        for filename in ('cohort.json', 'disclosure-availability.json', 'senate-extraction-audit.json', 'summary.json'):
            old = source / 'public/data' / filename
            if old.is_file():
                new = staging / 'metadata' / filename; new.parent.mkdir(exist_ok=True); shutil.copyfile(old, new)
        # The optional immutable day ledger is needed for saved overnight trade details.
        trade_index = source / 'public/data/overnight-trades.sqlite'
        if trade_index.is_file(): shutil.copyfile(trade_index, staging / 'overnight-trades.sqlite')
        policy_path = source / 'research/price-history-policies.json'
        if policy_path.is_file():
            policies = load_json(policy_path)
            new = staging / 'research/price-history-policies.json'; new.parent.mkdir(exist_ok=True); shutil.copyfile(policy_path, new)
            for policy in policies.get('policies', []):
                for evidence in policy.get('primarySources', []):
                    parts = Path(evidence['localPath']).parts
                    if Path(evidence['localPath']).is_absolute():
                        if 'congress-lab' not in parts: raise ValueError('Unrecognized policy evidence path')
                        parts = parts[parts.index('congress-lab')+1:]
                    if not parts or parts[0] != 'research' or '..' in parts: raise ValueError('Invalid policy evidence path')
                    old = source.joinpath(*parts).resolve()
                    if not old.is_relative_to(source): raise ValueError('Policy evidence escapes archive')
                    raw = old.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != evidence['sha256']: raise ValueError('Policy evidence checksum failed')
                    new = staging.joinpath(*parts); new.parent.mkdir(parents=True, exist_ok=True); new.write_bytes(raw)
        price_count = 0
        if include_prices:
            symbols.add('SPY')
            directory = staging / 'research/prices'; directory.mkdir(parents=True, exist_ok=True)
            for symbol in sorted(symbols):
                if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', symbol): continue
                for suffix in ('.json', '.raw.json'):
                    old = source / 'research/prices' / (symbol + suffix)
                    if old.is_file():
                        raw = old.read_bytes()
                        (directory / (old.name + '.gz')).write_bytes(gzip.compress(raw, compresslevel=3, mtime=0))
                        hashes['prices/'+old.name] = hashlib.sha256(raw).hexdigest(); price_count += 1
        from .lab_engine.price_history import ARCHIVE_ROOT, price_bars
        context = ARCHIVE_ROOT.set(staging)
        try:
            price_cutoff = max(price_bars('SPY'), default=None)
        finally:
            ARCHIVE_ROOT.reset(context)
        manifest = {'schema_version':1, 'engineProjectionVersion':2, 'imported_at':datetime.now(timezone.utc).isoformat(), 'records':sum(counts.values()), 'chambers':dict(counts), 'politicians':sorted(people.values(), key=lambda p:p['name']), 'startDate':earliest, 'snapshotDate':latest, 'priceCutoff':price_cutoff, 'priceFiles':price_count, 'sourceHashes':hashes, 'complete':False, 'coverageNote':'Retained official disclosure snapshot; unknown instruments and extraction gaps remain explicit. Filing dates are historical publication proxies, not verified first-public times.'}
        atomic_json(staging / 'manifest.json', manifest)
        if target.exists():
            if any(target.iterdir()):
                if not refresh or not (target/'manifest.json').is_file():
                    raise ValueError('Import target is not a recognized dataset; existing data was preserved')
                revisions = target.parent / 'congress-revisions'
                revisions.mkdir(exist_ok=True, mode=0o700)
                backup = revisions / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
                os.replace(target, backup)
            else:
                target.rmdir()
        try:
            os.replace(staging, target)
        except BaseException:
            if backup is not None and not target.exists(): os.replace(backup, target)
            raise
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


class LabData:
    def __init__(self, root: Path): self.root = root

    @property
    def available(self): return (self.root / 'congress.sqlite3').is_file() and (self.root / 'manifest.json').is_file()

    def manifest(self): return load_json(self.root / 'manifest.json', {})

    def connect(self):
        if not self.available: raise ValueError('Congress data has not been imported into this research workspace')
        return sqlite3.connect((self.root / 'congress.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)

    def query(self, *, chamber='all', politician='all', owner='all', action='all', date='9999-12-31', search='', eligible=None):
        terms = ['filed_date<=?']; values = [date]
        for field, value in (('chamber',chamber),('action',action)):
            if value != 'all': terms.append(field+'=?'); values.append(value)
        if owner == 'Unknown':
            terms.append("(owner IS NULL OR trim(owner)='' OR owner='Unknown')")
        elif owner != 'all':
            terms.append('owner=?'); values.append(owner)
        if politician != 'all':
            ids = politician.split(','); terms.append('politician_id IN ('+','.join('?' for _ in ids)+')'); values.extend(ids)
        if search.strip(): terms.append('instr(search,?)>0'); values.append(search.strip().casefold())
        if eligible is not None: terms.append('eligible=?'); values.append(int(eligible))
        return ' AND '.join(terms), values

    def rows(self, *, field='page', offset=None, limit=40, **filters):
        if field not in ('page','engine','payload'): raise ValueError('Invalid row projection')
        where, values = self.query(**filters)
        with closing(self.connect()) as db:
            total = db.execute('SELECT count(*) FROM records WHERE '+where, values).fetchone()[0]
            legacy_projection = field=='engine' and self.manifest().get('engineProjectionVersion',1)<2
            sql = 'SELECT '+field+(',payload' if legacy_projection else '')+' FROM records WHERE '+where+' ORDER BY filed_date DESC,trade_date DESC,id DESC'
            if offset is not None: sql += ' LIMIT ? OFFSET ?'; values = [*values, limit, offset]
            items = []
            for row in db.execute(sql, values):
                item = json.loads(zlib.decompress(row[0]) if isinstance(row[0], bytes) else row[0])
                if legacy_projection and item.get('filedAt') and 'filedTimeZoneAssumption' not in item:
                    original = json.loads(zlib.decompress(row[1]))
                    item['filedTimeZoneAssumption'] = original.get('filedTimeZoneAssumption')
                items.append(item)
        return {'items':items, 'total':total, 'offset':offset or 0, 'limit':limit}

    def record(self, record_id):
        with closing(self.connect()) as db: row = db.execute('SELECT payload FROM records WHERE id=?',(record_id,)).fetchone()
        if row is None: raise KeyError(record_id)
        return json.loads(zlib.decompress(row[0]))
