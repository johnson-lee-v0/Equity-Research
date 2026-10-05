#!/usr/bin/env python3
"""Check the exact static Pages artifact, without importing application services."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

_LOCAL_RUNTIME = re.compile(r"(?:/api/(?:office|runs|events|memory/sync)|new\s+EventSource\s*\()")
# The restored META example is intentionally public; live feeds and private
# application services are not. Content provenance is reviewed separately.
_PUBLIC_NETWORK = re.compile(r"market-news\.json|new\s+WebSocket\s*\(|sendBeacon\s*\(")


def check_artifact(root: Path) -> list[str]:
    errors = []
    if not root.is_dir():
        return ["Demo artifact directory is missing"]
    files = [path for path in root.rglob('*') if path.is_file() or path.is_symlink()]
    allowed_root = {'index.html', 'favicon.svg', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.txt'}
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
            if _PUBLIC_NETWORK.search(code):
                errors.append(f"Live feed or telemetry found in public script: {rel}")
    if not (root / 'index.html').is_file():
        errors.append('Demo index.html is missing')
    combined = '\n'.join(scripts)
    if not all(marker in combined for marker in ('Public demo', 'META', 'Understand the limits.', 'I understand', 'not personalized financial advice')):
        errors.append('Acknowledged META public demo entry was not found')
    for filename, markers in {
        'LICENSE.txt': ['MIT License', 'Copyright 2026 Johnson Lee', 'THE SOFTWARE IS PROVIDED "AS IS"'],
        'THIRD_PARTY_NOTICES.txt': ['react', 'Permission is hereby granted'],
    }.items():
        path = root / filename
        if path.is_symlink():
            continue
        try:
            content = path.read_text(encoding='utf-8')
            if not all(marker in content for marker in markers):
                errors.append(f'Required license content is missing: {filename}')
        except (OSError, UnicodeError):
            errors.append(f'Required license file is missing or unreadable: {filename}')
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
    print('Public demo artifact: static allowlist, notices and runtime isolation checks passed')
