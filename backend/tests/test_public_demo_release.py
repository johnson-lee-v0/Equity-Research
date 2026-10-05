"""Prevent publishing local state, live feeds or missing license/entry notices."""
from pathlib import Path

import pytest

from scripts.check_public_demo import check_artifact


def artifact(tmp_path: Path) -> Path:
    root = tmp_path / 'site'
    (root / 'assets').mkdir(parents=True)
    (root / 'index.html').write_text('<html><script src="./assets/demo.js"></script></html>')
    (root / 'assets/demo.js').write_text('console.log("Public demo", "META", "Understand the limits.", "I understand", "not personalized financial advice")')
    (root / 'assets/demo.css').write_text('body{color:green}')
    (root / 'LICENSE.txt').write_text('MIT License\nCopyright 2026 Johnson Lee\nTHE SOFTWARE IS PROVIDED "AS IS"')
    (root / 'THIRD_PARTY_NOTICES.txt').write_text('react\nPermission is hereby granted')
    return root


def test_allows_the_acknowledged_static_meta_artifact_with_notices(tmp_path):
    assert check_artifact(artifact(tmp_path)) == []


@pytest.mark.parametrize('entry', ['console.log("Public demo", "META")', 'console.log("Cedar Workshop")'])
def test_requires_the_meta_entry_and_acknowledgment_notices(tmp_path, entry):
    root = artifact(tmp_path)
    (root / 'assets/demo.js').write_text(entry)
    assert any('public demo entry was not found' in error for error in check_artifact(root))


@pytest.mark.parametrize('network', [
    'fetch("market-news.json")',
    'new WebSocket("wss://example.test")',
    'navigator.sendBeacon("https://example.test", "fixture")',
])
def test_rejects_live_feeds_and_telemetry(tmp_path, network):
    root = artifact(tmp_path)
    (root / 'assets/network.js').write_text(network)
    assert any('Live feed or telemetry' in error for error in check_artifact(root))


def test_allows_restored_real_company_facts_and_source_links(tmp_path):
    root = artifact(tmp_path)
    (root / 'assets/meta.js').write_text('console.log("META-Q2-2026-Earnings-Call-Transcript.pdf", "https://stockanalysis.com/stocks/meta/history/", 751.66, 60.801)')
    assert check_artifact(root) == []


def test_allows_trigonometry_used_by_memory_visualization(tmp_path):
    root = artifact(tmp_path)
    (root / 'assets/memory.js').write_text('export function position(angle) { return Math.sin(angle); }')
    assert check_artifact(root) == []


def test_rejects_private_exports_source_maps_backend_and_news_snapshots(tmp_path):
    root = artifact(tmp_path)
    for name in ['research.sqlite3', 'worker.py', 'assets/demo.js.map', 'market-news.json']:
        (root / name).write_text('test fixture')
    errors = check_artifact(root)
    assert len(errors) == 4
    assert all('Unexpected public artifact' in error for error in errors)


def test_local_application_build_cannot_pass_as_a_demo(tmp_path):
    root = artifact(tmp_path)
    (root / 'assets/demo.js').write_text('fetch("/api/office"); new EventSource("/api/events")')
    errors = check_artifact(root)
    assert any('Local research runtime' in error for error in errors)
    assert any('public demo entry was not found' in error for error in errors)


def test_rejects_symlink_without_reading_its_target(tmp_path):
    root = artifact(tmp_path)
    private = tmp_path / 'private.txt'
    private.write_text('synthetic private content')
    (root / 'assets/leak.js').symlink_to(private)
    errors = check_artifact(root)
    assert any('Symlink' in error for error in errors)
    assert all('synthetic private content' not in error for error in errors)


@pytest.mark.parametrize('filename', ['LICENSE.txt', 'THIRD_PARTY_NOTICES.txt'])
def test_requires_license_files(tmp_path, filename):
    root = artifact(tmp_path)
    (root / filename).unlink()
    assert any(filename in error for error in check_artifact(root))
