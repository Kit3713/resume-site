"""
Readiness probe tests — Phase 21.2

Covers ``GET /readyz`` and the four checks it runs:

* ``db_connect`` — fresh sqlite3 connection + ``SELECT 1``.
* ``migrations_current`` — every file in ``migrations/`` recorded in
  ``schema_version``.
* ``photos_writable`` — the configured ``PHOTO_STORAGE`` exists and is
  writable.
* ``disk_space`` — the database's host filesystem has at least
  ``RESUME_SITE_READYZ_MIN_FREE_MB`` (default 100MB) free.

The route short-circuits on the first failure, so each failure-mode
test asserts ``failed == "<check>"`` plus a 503; the success test
asserts every check ran and returned ``ok``. Coverage targets the
service module (``app.services.migrations``) for the migration helpers
and the route module for the probe orchestration.

A separate test verifies ``/readyz`` is excluded from analytics so
orchestrator polling does not pollute ``page_views`` (matches the
existing ``/healthz`` exclusion).
"""

from __future__ import annotations

import sqlite3
from collections import namedtuple

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json(response):
    """Parse the JSON body, asserting Content-Type so tests fail fast on HTML errors."""
    assert response.content_type.startswith('application/json'), response.content_type
    return response.get_json()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_readyz_returns_200_when_everything_ready(client, app):
    """The fixture app has a fresh DB + writable photos dir + lots of disk."""
    response = client.get('/readyz')
    assert response.status_code == 200
    body = _json(response)
    assert body['ready'] is True
    # Every check ran and returned 'ok' (the route serializes those as
    # the literal string 'ok' in the success branch).
    assert body['checks'] == {
        'db_connect': 'ok',
        'migrations_current': 'ok',
        'photos_writable': 'ok',
        'disk_space': 'ok',
    }


# ---------------------------------------------------------------------------
# db_connect failure
# ---------------------------------------------------------------------------


def test_readyz_503_when_db_connect_fails(client, app, monkeypatch):
    """Force sqlite3.connect to raise; the probe must report db_connect.

    The check function imports sqlite3 lazily, so patching the
    attribute on the cached module object intercepts the call site.
    """

    def _broken_connect(*args, **kwargs):
        raise sqlite3.OperationalError('database locked (test)')

    monkeypatch.setattr(sqlite3, 'connect', _broken_connect)

    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['ready'] is False
    assert body['failed'] == 'db_connect'
    assert 'OperationalError' in body['detail']


# ---------------------------------------------------------------------------
# migrations_current failure
# ---------------------------------------------------------------------------


def test_readyz_503_when_migrations_pending(client, app, monkeypatch):
    """Pretend a brand-new migration ships in the migrations directory."""
    from app.services import migrations as migrations_svc

    real_list = migrations_svc.list_migration_files

    def _list_with_extra(migrations_dir=None):
        # Append a fake newer migration that won't be in schema_version.
        return real_list(migrations_dir) + [(999, '999_test_pending.sql')]

    monkeypatch.setattr(migrations_svc, 'list_migration_files', _list_with_extra)

    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['failed'] == 'migrations_current'
    assert '999_test_pending.sql' in body['detail']
    # Earlier checks ran and reported ok.
    assert body['checks']['db_connect'] == 'ok'


# ---------------------------------------------------------------------------
# photos_writable failure
# ---------------------------------------------------------------------------


def test_readyz_503_when_photos_dir_missing(client, app):
    """Point PHOTO_STORAGE at a non-existent path."""
    app.config['PHOTO_STORAGE'] = '/nonexistent/readyz-test-path-' + 'x' * 8
    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['failed'] == 'photos_writable'
    assert 'directory missing' in body['detail']


def test_readyz_503_when_photos_dir_unwritable(client, app, tmp_path, monkeypatch):
    """``os.access`` returns False — covers the read-only-mount case."""
    import os as os_mod

    real_access = os_mod.access

    def _denying_access(path, mode):
        if mode == os_mod.W_OK and str(path) == app.config['PHOTO_STORAGE']:
            return False
        return real_access(path, mode)

    monkeypatch.setattr('app.routes.public.os.access', _denying_access)

    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['failed'] == 'photos_writable'
    assert 'not writable' in body['detail']


def test_readyz_503_when_photos_storage_unset(client, app):
    """An empty PHOTO_STORAGE config is treated as 'not configured'."""
    app.config['PHOTO_STORAGE'] = ''
    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['failed'] == 'photos_writable'
    assert 'not configured' in body['detail']


# ---------------------------------------------------------------------------
# disk_space failure
# ---------------------------------------------------------------------------


_DiskUsage = namedtuple('_DiskUsage', ('total', 'used', 'free'))


def test_readyz_503_when_disk_space_below_threshold(client, app, monkeypatch):
    """``shutil.disk_usage`` reports a sliver of free space."""
    import shutil

    monkeypatch.setattr(shutil, 'disk_usage', lambda _path: _DiskUsage(1_000, 999, 1))

    response = client.get('/readyz')
    assert response.status_code == 503
    body = _json(response)
    assert body['failed'] == 'disk_space'
    assert 'free=1' in body['detail']


def test_readyz_min_free_env_override(client, app, monkeypatch):
    """``RESUME_SITE_READYZ_MIN_FREE_MB`` lowers the threshold."""
    import shutil

    # Mock a filesystem with 2MB free. Default threshold (100MB) would
    # fail, but env override of 1MB should pass.
    monkeypatch.setattr(shutil, 'disk_usage', lambda _path: _DiskUsage(10_000_000, 8_000_000, 2_000_000))
    monkeypatch.setenv('RESUME_SITE_READYZ_MIN_FREE_MB', '1')

    response = client.get('/readyz')
    assert response.status_code == 200
    assert _json(response)['checks']['disk_space'] == 'ok'


def test_readyz_min_free_env_invalid_falls_back_to_default(client, app, monkeypatch):
    """Garbage env values fall back to the 100MB default — never raise."""
    import shutil

    # 200MB free easily clears the 100MB default.
    monkeypatch.setattr(
        shutil, 'disk_usage', lambda _path: _DiskUsage(10_000_000_000, 5_000_000_000, 200_000_000)
    )
    monkeypatch.setenv('RESUME_SITE_READYZ_MIN_FREE_MB', 'not-a-number')

    response = client.get('/readyz')
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Analytics exclusion
# ---------------------------------------------------------------------------


def test_readyz_not_recorded_in_page_views(client, app):
    """High-frequency probe traffic must not pollute the analytics table."""
    for _ in range(5):
        client.get('/readyz')

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM page_views WHERE path LIKE '/readyz%'"
        ).fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


# ---------------------------------------------------------------------------
# Service-module helpers (isolation tests)
# ---------------------------------------------------------------------------


def test_list_migration_files_skips_non_sql(tmp_path):
    """Files without a digit prefix or .sql extension are filtered out."""
    from app.services.migrations import list_migration_files

    (tmp_path / '001_first.sql').write_text('-- ok')
    (tmp_path / '002_second.sql').write_text('-- ok')
    (tmp_path / 'README.md').write_text('not a migration')
    # Digit prefix but no underscore separator -> int parse fails -> skipped.
    (tmp_path / '003-noprefix.sql').write_text('-- skipped: no underscore')
    (tmp_path / 'no_digit.sql').write_text('-- ignored')

    files = list_migration_files(str(tmp_path))
    versions = [v for v, _ in files]
    assert versions == [1, 2]


def test_get_applied_versions_handles_missing_table(tmp_path):
    """Probing a fresh DB returns an empty set, not an OperationalError."""
    from app.services.migrations import get_applied_versions

    db_path = tmp_path / 'fresh.db'
    conn = sqlite3.connect(str(db_path))
    try:
        assert get_applied_versions(conn) == set()
    finally:
        conn.close()


def test_get_pending_migrations_diffs_filesystem_vs_db(tmp_path):
    """A migration on disk but not in schema_version shows up as pending."""
    from app.services.migrations import (
        ensure_schema_version_table,
        get_pending_migrations,
    )

    migrations_dir = tmp_path / 'migrations'
    migrations_dir.mkdir()
    (migrations_dir / '001_a.sql').write_text('-- a')
    (migrations_dir / '002_b.sql').write_text('-- b')
    (migrations_dir / '003_c.sql').write_text('-- c')

    db_path = tmp_path / 'state.db'
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema_version_table(conn)
        conn.execute("INSERT INTO schema_version (version, name) VALUES (1, '001_a.sql')")
        conn.commit()

        pending = get_pending_migrations(conn, str(migrations_dir))
    finally:
        conn.close()

    assert [v for v, _ in pending] == [2, 3]


# ---------------------------------------------------------------------------
# Backwards-compatible CLI re-exports (regression guard)
# ---------------------------------------------------------------------------


def test_manage_py_re_exports_still_resolve():
    """Existing manage.py callers must keep working after the extraction."""
    import manage

    assert manage._get_migrations_dir().endswith('/migrations')
    files = manage._list_migration_files(manage._get_migrations_dir())
    # The repo ships at least the Phase 19 webhook migration.
    assert any('webhooks' in name for _, name in files)
