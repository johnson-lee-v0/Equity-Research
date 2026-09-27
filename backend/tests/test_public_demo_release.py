"""Prevent publishing local state or the ordinary app through the Pages artifact."""
import json
from pathlib import Path

import pytest

from scripts.check_public_demo import check_artifact


def artifact(tmp_path: Path) -> Path:
    root = tmp_path / 'site'
    (root / 'assets').mkdir(parents=True)
    (root / 'index.html').write_text('<html><script src="./assets/demo.js"></script></html>')
    (root / 'assets/demo.js').write_text('console.log("Public demo", "META")')
    (root / 'assets/demo.css').write_text('body{color:green}')
    (root / 'market-news.json').write_text(json.dumps({'status': 'unavailable', 'items': []}))
    return root


def test_allows_only_the_public_demo_static_artifact(tmp_path):
    assert check_artifact(artifact(tmp_path)) == []


@pytest.mark.parametrize('entry', ['console.log("Public demo", "ExampleCo")', 'console.log("META")'])
def test_requires_the_public_meta_case_entry(tmp_path, entry):
    root = artifact(tmp_path)
    (root / 'assets/demo.js').write_text(entry)
    assert any('public demo entry was not found' in error for error in check_artifact(root))


@pytest.mark.parametrize('retired_label', [
    'Fictional ExampleCo',
    'FICTIONAL EXAMPLECO',
    'Generated sample values to demonstrate chart controls.',
    'All 60 points are synthetic.',
])
def test_rejects_legacy_synthetic_valuation_even_with_valid_meta_entry(tmp_path, retired_label):
    root = artifact(tmp_path)
    (root / 'assets/old-valuation.js').write_text(f'console.log({json.dumps(retired_label)})')
    errors = check_artifact(root)
    assert errors == ['Legacy synthetic valuation found in public script: assets/old-valuation.js']


def test_allows_trigonometry_used_by_public_memory_visualization(tmp_path):
    root = artifact(tmp_path)
    (root / 'assets/memory.js').write_text('export function position(angle) { return Math.sin(angle); }')
    assert check_artifact(root) == []


def test_rejects_private_exports_backend_files_and_source_maps(tmp_path):
    root = artifact(tmp_path)
    (root / 'research.sqlite3').write_bytes(b'private fixture')
    (root / 'worker.py').write_text('pass')
    (root / 'assets/demo.js.map').write_text('{}')
    errors = check_artifact(root)
    assert len(errors) == 3
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


def test_requires_a_bounded_public_news_snapshot(tmp_path):
    root = artifact(tmp_path)
    (root / 'market-news.json').write_text(json.dumps({'status': 'fresh', 'items': [{}] * 7}))
    assert any('unexpected shape' in error for error in check_artifact(root))
    (root / 'market-news.json').unlink()
    assert any('missing or invalid' in error for error in check_artifact(root))
