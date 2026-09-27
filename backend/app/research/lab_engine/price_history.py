"""Shared price-cache validation for saved backtests and local snapshots."""
import functools, gzip, hashlib, json, pathlib
from contextvars import ContextVar

ARCHIVE_ROOT = ContextVar("lab_archive_root", default=None)

def root_path():
    root = ARCHIVE_ROOT.get()
    if root is None:
        raise ValueError("No congress data archive selected")
    return pathlib.Path(root)

def price_cache():
    return root_path() / "research/prices"

def read_bytes(path):
    return path.read_bytes() if path.is_file() else gzip.decompress(path.with_suffix(path.suffix + ".gz").read_bytes())

def exists(path):
    return path.is_file() or path.with_suffix(path.suffix + ".gz").is_file()
from .source_price_guards import audit_price_history, invalid_adjusted_close_indices




def resolve_evidence_path(saved_path):
    """Relocate historical absolute paths inside this checkout; hashes remain mandatory."""
    saved = pathlib.PurePosixPath(saved_path)
    parts = saved.parts
    if saved.is_absolute():
        if 'congress-lab' not in parts:
            raise ValueError('Unrecognized archived evidence path')
        parts = parts[parts.index('congress-lab') + 1:]
    if not parts or parts[0] != 'research' or '..' in parts:
        raise ValueError('Evidence must remain within the research archive')
    candidate = root_path().joinpath(*parts).resolve()
    if not candidate.is_relative_to(root_path().resolve()):
        raise ValueError('Evidence path escapes the checkout')
    return candidate


@functools.lru_cache(maxsize=4)
def _policies(root, signature):
    policy_path = pathlib.Path(root) / "research/price-history-policies.json"
    if not signature:
        return {}
    policies = json.loads(policy_path.read_text())['policies']
    for policy in policies:
        for source in policy['primarySources']:
            actual = hashlib.sha256(resolve_evidence_path(source['localPath']).read_bytes()).hexdigest()
            if actual != source['sha256']:
                raise ValueError('Price boundary source evidence changed: ' + policy['symbol'])
    return {p['symbol']: p for p in policies}


def reviewed_price_policies():
    path = root_path() / 'research/price-history-policies.json'
    return _policies(str(root_path()), path.stat().st_mtime_ns if path.exists() else 0)


def read_price_history(symbol, expected_sessions=None):
    path = price_cache() / (symbol + '.json')
    if not exists(path):
        return {'symbol': symbol, 'bars': [], 'issues': ['price_history_unavailable']}
    try:
        raw = read_bytes(path)
        cache = json.loads(raw)
    except (ValueError, OSError):
        return {'symbol': symbol, 'bars': [], 'issues': ['price_cache_unreadable']}
    if cache.get('symbol') != symbol:
        return {'symbol': symbol, 'bars': [], 'issues': ['price_cache_symbol_mismatch']}
    sha = hashlib.sha256(raw).hexdigest()
    policy = reviewed_price_policies().get(symbol, {'symbol': symbol, 'expectedCacheSha256': sha})
    result = audit_price_history(cache, policy, expected_sessions=expected_sessions, cache_sha256=sha)
    original_path=price_cache()/(symbol+'.raw.json')
    if exists(original_path):
        try:
            original=read_bytes(original_path)
            if hashlib.sha256(original).hexdigest()!=cache.get('rawSha256'):
                raise ValueError('original_adjustment_hash_mismatch')
            responses=json.loads(original)['chart']['result']
            if not isinstance(responses,list) or len(responses)!=1:
                raise ValueError('original_adjustment_result_count_mismatch')
            response=responses[0]
            if response['meta'].get('symbol')!=symbol:
                raise ValueError('original_adjustment_symbol_mismatch')
            invalid=invalid_adjusted_close_indices(response)
            if invalid:
                result.update(bars=[],retainedBars=0,historyComplete=False,historyQuarantined=True,
                              quarantineReason='Positive original quote paired with nonpositive or invalid adjusted close; the entire adjustment chain is unreliable.',
                              invalidAdjustmentTimestamps=[response['timestamp'][i] for i in invalid])
                result['issues'].append('invalid_adjusted_close_chain_entire_history_excluded')
        except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
            result.update(bars=[],retainedBars=0,historyComplete=False)
            result['issues'].append('original_adjustment_validation_failed:'+str(exc))
    if not result['bars']:
        result['issues'].append('price_history_unavailable')
    return result


def price_bars(symbol):
    return {b['date']: b for b in read_price_history(symbol)['bars']}
