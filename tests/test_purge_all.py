"""
purge-all CLI tests — Phase 25.1 (#42, #55, #62, #68)

Covers:

* Every table's purge runs in sequence.
* Each purge writes a ``purge_last_success_<table>`` timestamp so the
  admin dashboard can surface "when did this last run".
* A failure on one table does not abort the others (errors are
  collected; exit is non-zero after all purges complete).
* Retention windows are read from settings with sensible defaults.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys


def test_purge_all_runs_every_table_and_stamps_settings(tmp_path):
    """End-to-end: seed one too-old row into each retention-managed
    table, run ``python manage.py purge-all``, assert every row was
    purged and the ``purge_last_success_*`` settings were written."""
    from tests.conftest import _init_test_db, _write_test_config

    config_path = _write_test_config(tmp_path)
    _init_test_db(str(tmp_path / 'test.db'))

    # Seed old rows (created_at ~1 year ago) into every retention-managed
    # table. The defaults (90/30/30/90 days) are all exceeded. The
    # timestamp is computed server-side via ``strftime`` so no Python
    # string interpolation touches user input.
    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    old_ts = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-400 days')"
    # ruff S608 triggers on f-string → execute() but the interpolated
    # `old_ts` is a hardcoded SQL literal, not user input.
    pv_sql = f"INSERT INTO page_views (path, created_at) VALUES ('/old', {old_ts})"  # noqa: S608
    conn.execute(pv_sql)
    la_sql = (
        f"INSERT INTO login_attempts (ip_hash, success, created_at) VALUES ('abc', 0, {old_ts})"  # noqa: S608
    )
    conn.execute(la_sql)
    # webhook_deliveries has a FK to webhooks(id) — need a parent row first.
    conn.execute(
        'INSERT INTO webhooks (id, name, url, secret, events, enabled) '
        "VALUES (1, 'test', 'https://example/', 'x', '[\"*\"]', 1)"
    )
    wd_sql = f"INSERT INTO webhook_deliveries (webhook_id, event, status_code, created_at) VALUES (1, 'test', 200, {old_ts})"  # noqa: S608
    conn.execute(wd_sql)
    al_sql = f"INSERT INTO admin_activity_log (action, created_at) VALUES ('test', {old_ts})"  # noqa: S608
    conn.execute(al_sql)
    conn.commit()
    conn.close()

    # Run the CLI as a subprocess so we exercise the arg dispatcher.
    env = {**os.environ, 'RESUME_SITE_CONFIG': config_path}
    result = subprocess.run(
        [sys.executable, 'manage.py', 'purge-all'],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f'purge-all exited {result.returncode}: {result.stderr}'
    # Each table's purge should appear in stdout.
    for table in ('page_views', 'login_attempts', 'webhook_deliveries', 'admin_activity_log'):
        assert table in result.stdout, f'missing {table} in output: {result.stdout}'

    # Every seeded old row should be gone.
    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    try:
        assert conn.execute('SELECT COUNT(*) FROM page_views').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM webhook_deliveries').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM admin_activity_log').fetchone()[0] == 0
        # Freshness stamps written.
        for table in (
            'page_views',
            'login_attempts',
            'webhook_deliveries',
            'admin_activity_log',
        ):
            row = conn.execute(
                'SELECT value FROM settings WHERE key = ?',
                (f'purge_last_success_{table}',),
            ).fetchone()
            assert row is not None, f'missing purge_last_success_{table}'
            assert row[0].startswith('20'), f'bad timestamp for {table}: {row[0]!r}'
    finally:
        conn.close()
