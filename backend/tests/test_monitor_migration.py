from pathlib import Path
import json
import sqlite3

import pytest

from backend.app.config import Settings
from backend.app.db import Database

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('has_revision', [False, True])
def test_monitor_upgrade_retains_old_and_current_checkpoints(tmp_path: Path, has_revision: bool):
    migrations = tmp_path/'checkout/backend/migrations'
    migrations.mkdir(parents=True)
    for path in (ROOT/'backend/migrations').glob('*.sql'):
        if int(path.name.split('_')[0]) >= 12:
            continue
        sql = path.read_text()
        if path.name.startswith('011') and not has_revision:
            sql = sql.replace('    revision INTEGER NOT NULL DEFAULT 0,\n', '')
        (migrations/path.name).write_text(sql)
    config = Settings(project_root=tmp_path/'checkout', data_dir=tmp_path/'private')
    Database(config=config)
    state = json.dumps({'backfill_cursor':'t3_saved','backfill_cutoff':'2026-09-01T00:00:00Z','poll_cursor':'t3_overflow'})
    with sqlite3.connect(config.db_path) as conn:
        conn.execute('INSERT INTO intake_monitors(namespace,community,enabled,window_days,state_json,updated_at) VALUES(?,?,?,?,?,?)', ('real','wallstreetbets',1,7,state,'2026-09-08T00:00:00Z'))
    migration = ROOT/'backend/migrations/012_reddit_monitor_revision_compat.sql'
    (migrations/migration.name).write_text(migration.read_text())
    db = Database(config=config)
    with db.operation() as conn:
        row = conn.execute('SELECT * FROM intake_monitors').fetchone()
        assert (row['namespace'],row['community'],row['enabled'],row['window_days'],row['state_json'],row['updated_at']) == ('real','wallstreetbets',1,7,state,'2026-09-08T00:00:00Z')
        assert row['revision'] == 0
        conn.execute('UPDATE intake_monitors SET revision=4')
    Database(config=config)
    with db.operation() as conn:
        assert conn.execute('SELECT revision FROM intake_monitors').fetchone()[0] == 4
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
