#!/usr/bin/env python3
"""Check the exact static Pages artifact, without importing application services."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


# The source privacy gate runs separately. This guard additionally prevents a
# local-app build, source maps, arbitrary exports or backend files being uploaded.
_LOCAL_RUNTIME = re.compile(r"(?:/api/(?:office|runs|events|memory/sync)|new\s+EventSource\s*\()")


def check_artifact(root: Path) -> list[str]:
    errors = []
    if not root.is_dir():
        return ["Demo artifact directory is missing"]
    files = [path for path in root.rglob('*') if path.is_file() or path.is_symlink()]
    allowed_root = {'index.html', 'favicon.svg', 'market-news.json'}
    scripts = []
    for path in files:
        rel = path.relative_to(root)
        if path.is_symlink():
            errors.append(f"Symlink is not a publishable asset: {rel}")
            continue
        if rel.as_posix() not in allowed_root and not (len(rel.parts) == 2 and rel.parts[0] == 'assets' and path.suffix in {'.js', '.css'}):
            errors.append(f"Unexpected public artifact: {rel}")
            continue
        if path.suffix == '.js':
            code = path.read_text(encoding='utf-8')
            scripts.append(code)
            if _LOCAL_RUNTIME.search(code):
                errors.append(f"Local research runtime found in public script: {rel}")
    if not (root / 'index.html').is_file():
        errors.append('Demo index.html is missing')
    if not any('Public demo' in code and 'ExampleCo' in code for code in scripts):
        errors.append('Authored public demo entry was not found')
    try:
        snapshot = json.loads((root / 'market-news.json').read_text(encoding='utf-8'))
        if snapshot.get('status') not in {'fresh', 'stale', 'unavailable'} or not isinstance(snapshot.get('items'), list) or len(snapshot['items']) > 6:
            errors.append('Public news snapshot has an unexpected shape')
    except (OSError, ValueError, TypeError, AttributeError):
        errors.append('Public news snapshot is missing or invalid')
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    args = parser.parse_args()
    errors = check_artifact(args.artifact)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    print('Public demo artifact: static allowlist and runtime isolation checks passed')
