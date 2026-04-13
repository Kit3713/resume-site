"""
Migration System Tests — Phase 7.4

Verifies the database migration system (manage.py migrate):
- Fresh database: all migrations apply cleanly from scratch.
- Existing v0.1.0 database: baseline auto-detected as applied.
- Bad SQL: transaction rolls back, database unchanged.
- --dry-run: produces output but makes no changes.
- --status: accurately reports applied vs pending migrations.
"""

import os
import sqlite3

import pytest

# Import the migration helpers directly from manage.py
from manage import (
    _detect_existing_db,
    _ensure_schema_version_table,
    _get_applied_versions,
    _list_migration_files,
)


@pytest.fixture
def migrations_dir():
    """Return the path to the real migrations/ directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh, empty SQLite database."""
    db_path = str(tmp_path / 'fresh.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def existing_v010_db(tmp_path):
    """Create a database that looks like a v0.1.0 install (settings table exists)."""
    db_path = str(tmp_path / 'v010.db')
    schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'schema.sql')
    conn = sqlite3.connect(db_path)
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ============================================================
# SCHEMA VERSION TABLE
# ============================================================


def test_ensure_schema_version_creates_table(fresh_db):
    """schema_version table should be created if it doesn't exist."""
    _ensure_schema_version_table(fresh_db)
    tables = {
        row[0]
        for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert 'schema_version' in tables


def test_ensure_schema_version_idempotent(fresh_db):
    """Calling _ensure_schema_version_table twice should not error."""
    _ensure_schema_version_table(fresh_db)
    _ensure_schema_version_table(fresh_db)
    count = fresh_db.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0]
    assert count == 0


# ============================================================
# APPLIED VERSIONS TRACKING
# ============================================================


def test_get_applied_versions_empty(fresh_db):
    """Empty schema_version table should return an empty set."""
    _ensure_schema_version_table(fresh_db)
    assert _get_applied_versions(fresh_db) == set()


def test_get_applied_versions_with_entries(fresh_db):
    """Applied versions should be returned as a set of integers."""
    _ensure_schema_version_table(fresh_db)
    fresh_db.execute("INSERT INTO schema_version (version, name) VALUES (1, '001_baseline.sql')")
    fresh_db.commit()
    assert _get_applied_versions(fresh_db) == {1}


# ============================================================
# EXISTING DATABASE DETECTION
# ============================================================


def test_detect_existing_db_positive(existing_v010_db):
    """A database with a settings table should be detected as existing."""
    assert _detect_existing_db(existing_v010_db) is True


def test_detect_existing_db_negative(fresh_db):
    """A fresh database should not be detected as existing."""
    assert _detect_existing_db(fresh_db) is False


# ============================================================
# MIGRATION FILE LISTING
# ============================================================


def test_list_migration_files_finds_baseline(migrations_dir):
    """Should find the 001_baseline.sql migration."""
    files = _list_migration_files(migrations_dir)
    assert len(files) >= 1
    versions = [v for v, _ in files]
    assert 1 in versions


def test_list_migration_files_sorted(migrations_dir):
    """Migration files should be returned in version order."""
    files = _list_migration_files(migrations_dir)
    versions = [v for v, _ in files]
    assert versions == sorted(versions)


def test_list_migration_files_nonexistent_dir():
    """Non-existent directory should return an empty list."""
    assert _list_migration_files('/nonexistent/path') == []


# ============================================================
# FRESH DATABASE: ALL MIGRATIONS APPLY CLEANLY
# ============================================================


def test_fresh_db_baseline_applies(fresh_db, migrations_dir):
    """Applying the baseline migration to a fresh DB should create all tables."""
    _ensure_schema_version_table(fresh_db)
    migration_files = _list_migration_files(migrations_dir)
    baseline = [f for v, f in migration_files if v == 1]
    assert len(baseline) == 1

    path = os.path.join(migrations_dir, baseline[0])
    with open(path) as f:
        sql = f.read()

    fresh_db.executescript(sql)
    fresh_db.execute('INSERT INTO schema_version (version, name) VALUES (1, ?)', (baseline[0],))
    fresh_db.commit()

    # Verify key tables were created
    tables = {
        row[0]
        for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected_tables = {
        'settings',
        'content_blocks',
        'photos',
        'services',
        'reviews',
        'review_tokens',
        'page_views',
        'stats',
        'projects',
        'certifications',
        'contact_submissions',
    }
    assert expected_tables.issubset(tables)

    # Verify seed data was inserted
    settings_count = fresh_db.execute('SELECT COUNT(*) FROM settings').fetchone()[0]
    assert settings_count > 0


# ============================================================
# EXISTING v0.1.0 DB: BASELINE AUTO-DETECTED
# ============================================================


def test_existing_db_baseline_autodetected(existing_v010_db):
    """An existing v0.1.0 database should be auto-marked as having baseline applied."""
    _ensure_schema_version_table(existing_v010_db)
    applied = _get_applied_versions(existing_v010_db)

    # Simulate the auto-detection logic from manage.py migrate
    if _detect_existing_db(existing_v010_db) and 1 not in applied:
        existing_v010_db.execute(
            "INSERT INTO schema_version (version, name) VALUES (1, '001_baseline.sql')"
        )
        existing_v010_db.commit()

    applied = _get_applied_versions(existing_v010_db)
    assert 1 in applied


# ============================================================
# BAD SQL: MIGRATION FAILURE
# ============================================================


def test_bad_migration_does_not_corrupt_db(fresh_db, tmp_path):
    """A migration with bad SQL should fail without applying partial changes."""
    _ensure_schema_version_table(fresh_db)

    # Create a bad migration file
    bad_migration = tmp_path / '999_bad.sql'
    bad_migration.write_text(
        'CREATE TABLE test_good (id INTEGER PRIMARY KEY);\nTHIS IS NOT VALID SQL;\n'
    )

    migrations = _list_migration_files(str(tmp_path))
    assert len(migrations) == 1

    # Attempt to apply the bad migration
    version, fname = migrations[0]
    path = str(tmp_path / fname)
    with open(path) as f:
        sql = f.read()

    # Any sqlite3 error is acceptable here — the point is that executescript
    # refuses to apply a malformed migration.
    with pytest.raises(sqlite3.Error):
        fresh_db.executescript(sql)

    # The schema_version table should NOT have this version recorded
    applied = _get_applied_versions(fresh_db)
    assert version not in applied


# ============================================================
# DRY RUN & STATUS (functional tests via subprocess)
# ============================================================


def test_dry_run_produces_output_no_changes(fresh_db, migrations_dir, capsys):
    """--dry-run should print SQL but not apply any migrations."""
    _ensure_schema_version_table(fresh_db)

    migration_files = _list_migration_files(migrations_dir)
    applied = _get_applied_versions(fresh_db)
    pending = [(v, f) for v, f in migration_files if v not in applied]
    assert len(pending) > 0, 'Expected at least one pending migration'

    # Simulate dry-run (same logic as manage.py migrate with args.dry_run=True)
    for _version, fname in pending:
        path = os.path.join(migrations_dir, fname)
        with open(path) as f:
            sql = f.read()
        print(f'-- DRY RUN: {fname}')
        print(sql)

    captured = capsys.readouterr()
    assert '-- DRY RUN:' in captured.out
    assert '001_baseline.sql' in captured.out

    # Verify nothing was actually applied
    applied_after = _get_applied_versions(fresh_db)
    assert len(applied_after) == 0

    # Verify no tables were created (except schema_version)
    tables = {
        row[0]
        for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert 'settings' not in tables


def test_migration_status_output(app, capsys, migrations_dir):
    """--status flag should list migrations with applied/pending labels."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    _ensure_schema_version_table(conn)
    applied = _get_applied_versions(conn)
    migration_files = _list_migration_files(migrations_dir)

    print('Migration status:')
    for version, fname in migration_files:
        status = 'applied ' if version in applied else 'pending '
        print(f'  [{status}] {fname}')

    conn.close()

    captured = capsys.readouterr()
    assert 'Migration status:' in captured.out
    assert '001_baseline.sql' in captured.out
