"""Read one execution day from the optional, immutable overnight trade index."""
import datetime as dt
import functools
import hashlib
import pathlib
import re
import sqlite3


CASE_IDS = ('both:active', 'both:new_filings', 'house:active', 'house:new_filings',
            'senate:active', 'senate:new_filings', 'senate:prior_selected')
PRICE_CONVENTION = 'Adjusted USD prices and synthetic total-return units from the saved simulation; not unadjusted historical quotes or politician executions.'


class TradeIndexError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


@functools.lru_cache(maxsize=4)
def _presentation_sha(path, signature):
    return sha(path)


def daily_trades(query, index, presentation):
    """Complete ledger day, in original execution order, with no symbol netting."""
    if set(query) != {'case', 'date'} or any(len(v) != 1 for v in query.values()):
        raise TradeIndexError(400, 'Provide one case and one date parameter.')
    case_id, day = query['case'][0], query['date'][0]
    if case_id not in CASE_IDS:
        raise TradeIndexError(404, 'Unknown overnight research case.')
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError()
        dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise TradeIndexError(400, 'Use a valid YYYY-MM-DD trading date.') from None
    index, presentation = pathlib.Path(index), pathlib.Path(presentation)
    if not index.is_file():
        raise TradeIndexError(503, 'Trade details require the optional local overnight trade index. Restore a current runtime pack or run scripts/export_overnight_trades.py.')
    try:
        stat = presentation.stat()
        current_sha = _presentation_sha(presentation, (stat.st_mtime_ns, stat.st_size, stat.st_ino))
        with sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            meta = dict(db.execute('SELECT key, value FROM metadata'))
            if meta.get('schemaVersion') != '1' or meta.get('presentationSha256') != current_sha:
                raise TradeIndexError(503, 'Trade details do not match the displayed research snapshot. Rebuild the overnight trade index.')
            case = db.execute('SELECT * FROM cases WHERE case_id=?', (case_id,)).fetchone()
            if case is None:
                raise TradeIndexError(404, 'This research case is not in the local trade index.')
            summary = db.execute('SELECT * FROM days WHERE case_id=? AND date=?', (case_id, day)).fetchone()
            if summary is None:
                raise TradeIndexError(404, 'This date is not a session in the research curve.')
            rows = db.execute('SELECT sequence, action, phase, symbol, price, units, notional, fee FROM trades WHERE case_id=? AND date=? ORDER BY sequence', (case_id, day)).fetchall()
            return {'caseId': case_id, 'date': day, 'trades': [dict(r) for r in rows],
                    'buyCount': summary['buy_count'], 'sellCount': summary['sell_count'],
                    'buyNotional': summary['buy_notional'], 'sellNotional': summary['sell_notional'],
                    'priceConvention': PRICE_CONVENTION,
                    'source': {'ledgerPath': case['ledger_path'], 'ledgerSha256': case['ledger_sha256'],
                               'presentationSha256': meta['presentationSha256']}}
    except TradeIndexError:
        raise
    except (OSError, sqlite3.Error):
        raise TradeIndexError(503, 'The local overnight trade index is unavailable or invalid. Restore or rebuild it.') from None
