"""Value-blind checks for the source publication gate."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import zipfile
from decimal import Decimal
from pathlib import Path

from scripts.release_privacy import PrivateMarkers, _scan_content, build_report, path_policy_category


ROOT = Path(__file__).resolve().parents[2]


def _private_fixture(root: Path) -> tuple[Path, Path, str, str, str]:
    root.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    data_dir.mkdir()
    env_path = root / ".env"
    synthetic_secret = "fixture-" + "credential-" + "token-42"
    env_path.write_text('API_TOKEN="%s"\n' % synthetic_secret, encoding="utf-8")

    synthetic_amount = Decimal("2000") + Decimal("0.37")
    amount_text = format(synthetic_amount, ".2f")
    account_id = "fixture-account-42"
    db_path = data_dir / "road2m.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE accounts (id TEXT, label TEXT, namespace TEXT)"
    )
    conn.execute(
        "CREATE TABLE balance_observations (amount TEXT, account_id TEXT, namespace TEXT)"
    )
    conn.execute(
        "CREATE TABLE positions (account_id TEXT, quantity TEXT, cost_basis TEXT, "
        "market_value TEXT, namespace TEXT)"
    )
    conn.execute(
        "CREATE TABLE app_settings (key TEXT, value_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE runs (input_snapshot_json TEXT)"
    )
    conn.execute("INSERT INTO accounts VALUES (?, ?, ?)", (account_id, "Fixture account", "real"))
    conn.execute(
        "INSERT INTO balance_observations VALUES (?, ?, ?)",
        (amount_text, account_id, "real"),
    )
    conn.execute(
        "INSERT INTO positions VALUES (?, ?, ?, ?, ?)",
        (account_id, "3", "17.25", amount_text, "real"),
    )
    conn.execute(
        "INSERT INTO app_settings VALUES (?, ?)",
        ("portfolio_policy", json.dumps({"cash": amount_text, "max_positions": 10})),
    )
    conn.execute(
        "INSERT INTO runs VALUES (?)",
        (json.dumps({"portfolio_snapshot": {"cash": amount_text, "account_id": account_id}}),),
    )
    # Demo/public rows must not become private markers merely because they use
    # the same schema and a monetary column name.
    conn.execute(
        "INSERT INTO balance_observations VALUES (?, ?, ?)",
        ("9999.91", "public-fixture", "demo"),
    )
    conn.commit()
    conn.close()
    return data_dir, env_path, amount_text, account_id, synthetic_secret


def _safe_public_tree(root: Path) -> tuple[Path, Path, Path]:
    tree = root / "publication"
    tree.mkdir()
    source = tree / "public.txt"
    source.write_text("Public source fixture.\n", encoding="utf-8")
    manifest = root / "manifest.txt"
    manifest.write_text("public.txt\n", encoding="utf-8")
    archive = root / "publication.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(source, "public.txt")
    return tree, manifest, archive


def test_gate_matches_grouped_json_money_credentials_and_account_records_without_leaking_values(tmp_path: Path) -> None:
    data_dir, env_path, amount_text, account_id, synthetic_secret = _private_fixture(tmp_path)
    tree = tmp_path / "publication"
    tree.mkdir()
    candidate = tree / "public.txt"
    candidate.write_text(
        json.dumps(
            {
                "cash": "%s" % format(Decimal(amount_text), ",.2f"),
                "quoted_cash": amount_text,
                "account": account_id,
                "credential": synthetic_secret,
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("public.txt\n", encoding="utf-8")
    archive = tmp_path / "publication.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(candidate, "public.txt")

    report = build_report(
        root=tree,
        manifest=manifest,
        archive=archive,
        private_root=tmp_path,
        data_dir=data_dir,
        env_paths=[env_path],
        include_git_history=False,
    )
    assert not report["pass"]
    categories = {row["category"] for row in report["hits"]}
    assert {"monetary_value", "credential_value", "personal_value"} <= categories
    serialized = json.dumps(report, sort_keys=True)
    assert amount_text not in serialized
    assert account_id not in serialized
    assert synthetic_secret not in serialized
    assert all(set(row) == {"path", "category", "count"} for row in report["hits"])


def test_public_policy_caps_and_generic_account_labels_are_not_owner_markers(tmp_path: Path) -> None:
    data_dir, env_path, _amount_text, _account_id, _synthetic_secret = _private_fixture(tmp_path)
    tree = tmp_path / "publication"
    tree.mkdir()
    policy_cap = Decimal("6400") + Decimal("0.25")
    candidate = tree / "policy.txt"
    candidate.write_text(
        json.dumps(
            {
                "limits": {
                    "long_term": {"initial_notional": format(policy_cap, ".2f")},
                    "trade": {"planned_loss_limit": format(policy_cap / 10, ".2f")},
                },
                "account_type": "TFSA",
                "label": "CAD/USD chequing",
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("policy.txt\n", encoding="utf-8")
    report = build_report(
        root=tree,
        manifest=manifest,
        private_root=tmp_path,
        data_dir=data_dir,
        env_paths=[env_path],
        include_git_history=False,
    )
    assert report["pass"]


def test_json_credential_configuration_is_scanned_without_collecting_public_fields(tmp_path: Path) -> None:
    data_dir, _env_path, _amount_text, _account_id, synthetic_secret = _private_fixture(tmp_path)
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": "public-provider-name",
                "client_secret": synthetic_secret,
                "token_count": 9999,
            }
        ),
        encoding="utf-8",
    )
    tree = tmp_path / "publication"
    tree.mkdir()
    (tree / "public.txt").write_text(synthetic_secret, encoding="utf-8")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("public.txt\n", encoding="utf-8")
    report = build_report(
        root=tree,
        manifest=manifest,
        private_root=tmp_path,
        data_dir=data_dir,
        env_paths=[config_path],
        include_git_history=False,
    )
    assert not report["pass"]
    assert any(row["category"] == "credential_value" for row in report["hits"])
    assert all(row["category"] != "monetary_value" for row in report["hits"])


def test_gate_rejects_private_paths_and_allows_explicit_clean_install(tmp_path: Path) -> None:
    tree, manifest, archive = _safe_public_tree(tmp_path)
    (tree / "exports").mkdir()
    private_file = tree / "exports" / "saved.txt"
    private_file.write_text("local export", encoding="utf-8")
    manifest.write_text("public.txt\nexports/saved.txt\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "a") as output:
        output.write(private_file, "backups/saved.tar.gz")

    report = build_report(
        root=tree,
        manifest=manifest,
        archive=archive,
        private_root=tmp_path / "empty-private-root",
        data_dir=tmp_path / "empty-private-root" / "data",
        include_git_history=False,
    )
    assert not report["pass"]
    assert any(row["category"] == "private_path" for row in report["hits"])
    assert any(row["category"] == "private_database_missing" for row in report["hits"])

    # The clean fixture still has a valid manifest; use only the public file so
    # the explicit empty-input mode demonstrates its intended contract.
    (tmp_path / "clean-manifest.txt").write_text("public.txt\n", encoding="utf-8")
    clean_report = build_report(
        root=tree,
        manifest=tmp_path / "clean-manifest.txt",
        private_root=tmp_path / "empty-clean-root",
        data_dir=tmp_path / "empty-clean-root" / "data",
        allow_empty=True,
        include_git_history=False,
    )
    assert clean_report["pass"]


def test_reachable_git_history_is_scanned_without_traversing_unreachable_objects(tmp_path: Path) -> None:
    data_dir, env_path, amount_text, _account_id, synthetic_secret = _private_fixture(tmp_path / "inputs")
    git_root = tmp_path / "publication"
    git_root.mkdir()
    source = git_root / "public.txt"
    source.write_text("Public source fixture.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(git_root)], check=True)
    subprocess.run(
        ["git", "-C", str(git_root), "config", "user.email", "fixture@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(git_root), "config", "user.name", "Release Fixture"],
        check=True,
    )
    history_file = git_root / "old-private.txt"
    history_file.write_text(
        "%s %s\n" % (amount_text, synthetic_secret), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(git_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(git_root), "commit", "-qm", "fixture"], check=True
    )
    history_file.unlink()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("public.txt\n", encoding="utf-8")

    report = build_report(
        root=git_root,
        manifest=manifest,
        private_root=tmp_path / "inputs",
        data_dir=data_dir,
        env_paths=[env_path],
        git_root=git_root,
    )
    assert not report["pass"]
    assert any(row["path"].startswith("git:") for row in report["hits"])
    serialized = json.dumps(report, sort_keys=True)
    assert amount_text not in serialized
    assert synthetic_secret not in serialized


def test_publication_path_policy_covers_private_directories_and_archive_formats() -> None:
    for path in (
        "exports/records.json",
        "backups/old.tar.gz",
        "tmp/debug.txt",
        ".cache/state",
        ".runtime/pnpm/node_modules/.bin/pnpm",
        "snapshot.tgz",
        "snapshot.tar",
        "snapshot.db-wal",
        ".env",
    ):
        assert path_policy_category(path) == "private_path"
    assert path_policy_category(".env.example") is None


def _money_hits(content: str, *private_values: str) -> dict:
    markers = PrivateMarkers()
    for value in private_values:
        markers.add("monetary_value", value, numeric=True)
    hits = {}
    _scan_content(content.encode(), "public-source", markers, hits)
    return hits


def test_numeric_values_match_whole_numbers_not_decimal_version_ip_or_grouped_fragments():
    assert not _money_hits('cash = 47.91', '47')
    assert not _money_hits('balance = 147.91', '47.91')
    assert not _money_hits('balance = 0.47', '47')
    assert not _money_hits('balance = "1,247.91"', '247.91')
    assert not _money_hits('package@2.47.91 host=127.47.91.8', '47.91')
    assert not _money_hits('.panel { width: 47px; padding: 47.91px }', '47', '47.91')
    assert _money_hits('cash = 47.00', '47')
    assert _money_hits('cash = "4.7e1"', '47')
    assert _money_hits('balance = "2,047.91"', '2047.91')
    assert _money_hits('2047.9100', '2047.91')


def test_short_whole_observations_require_account_context_without_file_or_value_allowlists():
    assert not _money_hits('max_length=47\nlimit = 47\n"synthetic_price": 47\nVersion 47', '47')
    for text in (
        '{"cash": 47}', 'cash = Decimal("47")', 'Cash balance\n47',
        'quantity:\n "47"', '{"position": {"value": "47"}}',
        '{"portfolio_snapshot": {\n "amount": 47\n}}',
        'Account | Balance\nFixture brokerage | 47',
        'Holding\tQuantity\nFixture ticker\t47',
        'holdings: 47', 'account balance: 47', 'cost_basis = "47"',
        'cash: 47.000000',
        '{"cash":"$47"}', 'Cash balance: USD 47', 'Cash balance: €47',
        'Cash balance: £47', 'Cash balance: ¥47', 'Position shares: 47',
        'Account,Balance\nExample brokerage,47',
        '{"portfolio":{"account":{"name":"Example"},"value":47}}',
        '"Account","Balance"\n"Example","47"',
    ):
        assert _money_hits(text, '47'), text
    assert not _money_hits('account = {"name": "Example"}\nlimit = 47', '47')
    assert _money_hits('quantity: 3', '3')
    assert not _money_hits('max_length=3', '3')
    assert not _money_hits('const matcher = /account|balance/;\nconst maxLength = 47', '47')
    assert not _money_hits('type Field = "account" | "balance";\nconst maxLength = 47', '47')
    for flag in ('0', '0.00', '1', '1.000000'):
        assert not _money_hits('cash: '+flag, flag)
    assert _money_hits('Cash balance: ($47)', '-47')
    assert _money_hits('Cash balance: (47)', '-47')
    for currency in ('€', '£', '¥', 'USD '):
        assert _money_hits('Cash balance: ('+currency+'47)', '-47')
        assert not _money_hits('Cash balance: ('+currency+'47)', '47')


def test_precise_amounts_account_identifiers_and_credentials_still_match_without_context():
    assert _money_hits('A naked observation: 47.91', '47.91')
    assert _money_hits('A naked observation: 2047', '2047')
    assert not _money_hits('Rounded observation: 47.91', '47.910001')
    markers = PrivateMarkers()
    markers.add('personal_value', 'fixture-account-identifier')
    markers.add('credential_value', 'fixture.access.token')
    hits = {}
    _scan_content(b'fixture-account-identifier fixture.access.token', 'style.css', markers, hits)
    assert {category for _, category in hits} == {'personal_value', 'credential_value'}
    assert _money_hits('Account balance: ($2,047.91)', '-2047.91')
    assert not _money_hits('Account balance: -2047.91', '2047.91')


def test_whole_numeric_privacy_applies_to_manifest_archive_and_reachable_history(tmp_path: Path):
    data_dir, env_path, *_ = _private_fixture(tmp_path / 'inputs')
    with sqlite3.connect(data_dir / 'road2m.sqlite3') as connection:
        connection.execute('INSERT INTO balance_observations VALUES (?, ?, ?)', ('47', 'fixture-other', 'real'))
    tree, manifest, archive = _safe_public_tree(tmp_path)
    (tree / 'public.txt').write_text('Cash balance\n47\n', encoding='utf-8')
    with zipfile.ZipFile(archive, 'w') as output:
        output.write(tree / 'public.txt', 'public.txt')
    subprocess.run(['git', 'init', '-q', str(tree)], check=True)
    subprocess.run(['git', '-C', str(tree), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tree), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Synthetic privacy fixture'], check=True)
    report = build_report(root=tree, manifest=manifest, archive=archive, private_root=tmp_path / 'inputs', data_dir=data_dir, env_paths=[env_path], git_root=tree)
    paths = {row['path'] for row in report['hits'] if row['category'] == 'monetary_value'}
    assert 'public.txt' in paths
    assert 'archive:public.txt' in paths
    assert 'git:public.txt' in paths
    assert 'git:index:public.txt' in paths
    assert all(set(row) == {'path', 'category', 'count'} for row in report['hits'])
