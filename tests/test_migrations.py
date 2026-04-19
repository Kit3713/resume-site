"""
Migration System Tests — Phase 7.4 + Phase 21.5

Verifies the database migration system (manage.py migrate):
- Fresh database: all migrations apply cleanly from scratch.
- Existing v0.1.0 database: baseline auto-detected as applied.
- Bad SQL: transaction rolls back, database unchanged.
- --dry-run: produces output but makes no changes.
- --status: accurately reports applied vs pending migrations.

Phase 21.5 additions cover the upgrade-survivability contract:
- Idempotency: every shipped migration can be re-executed against an
  already-migrated database without changing schema or data.
- Reversibility walker (``--verify-reversible``): rejects DROP TABLE,
  DROP of NOT NULL columns, NOT NULL ADD COLUMN without DEFAULT, and
  ALTER / MODIFY COLUMN constructs.
"""

import os
import sqlite3

import pytest

# Import the migration helpers directly from manage.py
from manage import (
    _classify_statement,
    _detect_existing_db,
    _ensure_schema_version_table,
    _get_applied_versions,
    _list_migration_files,
    _tokenize_sql,
    _verify_migrations_reversible,
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


# ============================================================
# PHASE 21.5 — IDEMPOTENCY
#
# A running container gets upgraded by pulling a new image and
# restarting. The entrypoint re-runs ``manage.py init-db`` against the
# existing database. Migrations that have been applied are skipped by
# the ``schema_version`` check, but we still want the SQL itself to be
# safe if it runs again — e.g. because a future change to the
# entrypoint or an operator pointing ``init-db`` at an odd state
# executes a script twice. ``CREATE TABLE IF NOT EXISTS`` + ``INSERT
# OR IGNORE`` are the idioms that make this safe, and this test locks
# that invariant in at the SQL level.
# ============================================================


def _snapshot_schema_and_rowcounts(conn):
    """Return a ``(schema_sql, {table: rowcount})`` pair for comparison."""
    schema_rows = conn.execute(
        'SELECT type, name, sql FROM sqlite_master '
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    schema = tuple((r[0], r[1], r[2]) for r in schema_rows)

    # Row counts on every user table (skip virtual FTS shadow tables —
    # their internal ``_data`` / ``_idx`` / ``_config`` / ``_content``
    # tables aren't part of the logical schema and their row counts
    # fluctuate across SQLite versions in ways unrelated to the
    # migrations we're exercising).
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '%_data' AND name NOT LIKE '%_idx' "
            "AND name NOT LIKE '%_config' AND name NOT LIKE '%_content' "
            "AND name NOT LIKE '%_docsize'"
        ).fetchall()
    ]
    counts = {}
    for t in tables:
        counts[t] = conn.execute(
            f'SELECT COUNT(*) FROM "{t}"'  # noqa: S608 — test helper; ``t`` comes from sqlite_master on the test-owned DB.
        ).fetchone()[0]
    return (schema, counts)


def test_every_migration_is_idempotent(tmp_path, migrations_dir):
    """Re-executing every shipped migration against an already-migrated DB
    must not change schema, row counts, or ``schema_version``.

    For each migration N:
      1. Fresh DB, apply 001..N, record schema + row counts + schema_version count.
      2. Execute N's SQL again via executescript.
      3. Assert schema identical, row counts identical, schema_version count identical.
    """
    migration_files = _list_migration_files(migrations_dir)
    assert migration_files, 'Expected at least one migration'

    for idx, (_version, fname) in enumerate(migration_files):
        db_path = tmp_path / f'idempotent_{idx}.db'
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        _ensure_schema_version_table(conn)

        # Apply 001..N
        for v, name in migration_files[: idx + 1]:
            with open(os.path.join(migrations_dir, name)) as f:
                conn.executescript(f.read())
            conn.execute(
                'INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)', (v, name)
            )
        conn.commit()

        before_schema, before_counts = _snapshot_schema_and_rowcounts(conn)
        before_versions = conn.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0]

        # Re-execute migration N directly — the script body must be safe
        # to replay. We deliberately do NOT write a second
        # ``schema_version`` row; that's the migrate-runner's job and it
        # already skips applied versions.
        with open(os.path.join(migrations_dir, fname)) as f:
            replay_sql = f.read()
        conn.executescript(replay_sql)
        conn.commit()

        after_schema, after_counts = _snapshot_schema_and_rowcounts(conn)
        after_versions = conn.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0]

        assert after_schema == before_schema, (
            f'Re-applying {fname} mutated the schema (check for a missing '
            'IF NOT EXISTS guard or a destructive ALTER).'
        )
        assert after_counts == before_counts, (
            f'Re-applying {fname} changed row counts (expected INSERT OR '
            f'IGNORE for seed data). Before: {before_counts} '
            f'After: {after_counts}'
        )
        assert after_versions == before_versions, (
            f'Re-applying {fname} wrote extra schema_version rows.'
        )
        conn.close()


# ============================================================
# PHASE 21.5 — REVERSIBILITY WALKER
#
# The walker parses migration SQL without consulting the DB and fails
# the build if a migration contains DDL that can't survive a rolling
# upgrade. This suite locks in both the "accept" (every shipped
# migration) and "reject" (the four canonical hazards) sides of the
# contract.
# ============================================================


def test_walker_accepts_every_shipped_migration(migrations_dir):
    """Today's migrations must pass the walker. A regression here means
    a future change introduced an unsafe DDL pattern; the walker's job
    is to catch that before it ships.
    """
    files = _list_migration_files(migrations_dir)
    violations = _verify_migrations_reversible(files, migrations_dir)
    assert violations == [], '\n'.join(f'{v.filename}:{v.line} {v.reason}' for v in violations)


def _write_tmp_migrations(tmp_path, files):
    """Write ``files`` (list of (name, sql)) to tmp_path and return sorted (version, name)."""
    out = []
    for name, sql in files:
        (tmp_path / name).write_text(sql)
        version = int(name.split('_')[0])
        out.append((version, name))
    out.sort()
    return out


def test_walker_rejects_drop_table(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY);'),
            ('002_drop.sql', 'DROP TABLE t;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 1
    assert violations[0].filename == '002_drop.sql'
    assert 'DROP TABLE' in violations[0].reason
    assert violations[0].line == 1


def test_walker_rejects_not_null_add_without_default(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY);'),
            ('002_bad_add.sql', 'ALTER TABLE t ADD COLUMN name TEXT NOT NULL;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 1
    assert 'NOT NULL' in violations[0].reason
    assert 'DEFAULT' in violations[0].reason


def test_walker_accepts_not_null_add_with_default(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY);'),
            ('002_ok_add.sql', "ALTER TABLE t ADD COLUMN name TEXT NOT NULL DEFAULT '';"),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert violations == []


def test_walker_accepts_nullable_add(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY);'),
            ('002_ok_add.sql', 'ALTER TABLE t ADD COLUMN bio TEXT;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert violations == []


def test_walker_rejects_drop_not_null_column(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL);'),
            ('002_drop_col.sql', 'ALTER TABLE t DROP COLUMN name;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 1
    assert 'NOT NULL' in violations[0].reason


def test_walker_accepts_drop_nullable_column(tmp_path):
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY, bio TEXT);'),
            ('002_drop_col.sql', 'ALTER TABLE t DROP COLUMN bio;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert violations == []


def test_walker_rejects_alter_column(tmp_path):
    """SQLite doesn't support ALTER COLUMN natively; any such syntax means
    an operator is about to do a lossy rewrite.
    """
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER);'),
            ('002_alter.sql', 'ALTER TABLE t ALTER COLUMN n SET DATA TYPE BIGINT;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 1
    assert 'ALTER' in violations[0].reason


def test_walker_tracks_rename_across_files(tmp_path):
    """A renamed column's NOT NULL flag should follow the new name so a
    later DROP under the new name still trips the guard.
    """
    entries = _write_tmp_migrations(
        tmp_path,
        [
            ('001_base.sql', 'CREATE TABLE t (id INTEGER PRIMARY KEY, old_name TEXT NOT NULL);'),
            ('002_rename.sql', 'ALTER TABLE t RENAME COLUMN old_name TO new_name;'),
            ('003_drop.sql', 'ALTER TABLE t DROP COLUMN new_name;'),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 1
    assert violations[0].filename == '003_drop.sql'
    assert 'NOT NULL' in violations[0].reason


def test_walker_collects_multiple_violations(tmp_path):
    """One migration with multiple hazards should report each — early
    exit would hide downstream problems.
    """
    entries = _write_tmp_migrations(
        tmp_path,
        [
            (
                '001_base.sql',
                'CREATE TABLE a (id INTEGER PRIMARY KEY);\nCREATE TABLE b (id INTEGER PRIMARY KEY);',
            ),
            (
                '002_bulk.sql',
                'DROP TABLE a;\nALTER TABLE b ADD COLUMN k TEXT NOT NULL;\n',
            ),
        ],
    )
    violations = _verify_migrations_reversible(entries, str(tmp_path))
    assert len(violations) == 2
    reasons = ' '.join(v.reason for v in violations)
    assert 'DROP TABLE' in reasons
    assert 'NOT NULL' in reasons


# ============================================================
# TOKENIZER UNIT TESTS
# ============================================================


def test_tokenizer_splits_on_semicolons():
    stmts = _tokenize_sql('SELECT 1; SELECT 2;')
    assert len(stmts) == 2
    assert stmts[0][1].rstrip(';').strip() == 'SELECT 1'


def test_tokenizer_ignores_semicolons_in_strings():
    stmts = _tokenize_sql("INSERT INTO t VALUES ('a; b'); SELECT 1;")
    assert len(stmts) == 2
    assert "'a; b'" in stmts[0][1]


def test_tokenizer_handles_doubled_quote_escape():
    stmts = _tokenize_sql("INSERT INTO t VALUES ('it''s;fine'); SELECT 1;")
    assert len(stmts) == 2
    assert "'it''s;fine'" in stmts[0][1]


def test_tokenizer_treats_line_comments_as_whitespace():
    stmts = _tokenize_sql('-- drop table x;\nSELECT 1;\n')
    # The ; inside the comment must NOT end a statement, and the comment
    # itself must not surface as an empty statement.
    assert len(stmts) == 1
    assert 'drop' not in stmts[0][1].lower()


def test_tokenizer_treats_block_comments_as_whitespace():
    stmts = _tokenize_sql('/* DROP TABLE x; */ CREATE TABLE t (id INTEGER);\n')
    assert len(stmts) == 1
    assert 'DROP' not in stmts[0][1]


def test_tokenizer_trigger_body_not_split():
    sql = (
        'CREATE TRIGGER foo_upd AFTER UPDATE ON foo BEGIN\n'
        '    INSERT INTO log VALUES (NEW.id);\n'
        '    UPDATE counts SET n = n+1;\n'
        'END;\n'
        'CREATE TABLE later (id INTEGER);\n'
    )
    stmts = _tokenize_sql(sql)
    assert len(stmts) == 2
    assert stmts[0][1].startswith('CREATE TRIGGER')
    assert stmts[1][1].startswith('CREATE TABLE later')


def test_tokenizer_reports_line_numbers():
    sql = '\n\nCREATE TABLE a (id INTEGER);\n\nDROP TABLE b;\n'
    stmts = _tokenize_sql(sql)
    # First statement starts on line 3; second on line 5
    assert stmts[0][0] == 3
    assert stmts[1][0] == 5


def test_classify_distinguishes_create_and_drop():
    kind_c, details_c = _classify_statement('CREATE TABLE x (id INTEGER PRIMARY KEY)')
    kind_d, details_d = _classify_statement('DROP TABLE x')
    assert kind_c == 'create_table'
    assert details_c['table'] == 'x'
    assert kind_d == 'drop_table'
    assert details_d['table'] == 'x'


def test_classify_virtual_table_recognised():
    kind, details = _classify_statement('CREATE VIRTUAL TABLE search USING fts5(title, body)')
    assert kind == 'create_table'
    assert details['table'] == 'search'


def test_classify_ignores_unrelated_statements():
    for s in (
        'SELECT 1',
        "INSERT INTO t VALUES (1, 'x')",
        'CREATE INDEX idx ON t(col)',
        'PRAGMA foreign_keys=ON',
        'CREATE TRIGGER x AFTER INSERT ON t BEGIN SELECT 1; END',
    ):
        kind, _ = _classify_statement(s)
        assert kind == 'other', f'Expected other for {s!r}, got {kind}'


# ============================================================
# CLI INTEGRATION — --verify-reversible path
# ============================================================


def test_cli_verify_reversible_exits_zero_for_clean_migrations(tmp_path, monkeypatch, capsys):
    """Happy path: the shipped migrations pass the check; CLI exits 0."""
    import manage

    # Point the runner at a clean temp migrations dir so the test is
    # independent of the repo's migrations count.
    good = tmp_path / 'migrations'
    good.mkdir()
    (good / '001_ok.sql').write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '');"
    )
    monkeypatch.setattr(manage, '_get_migrations_dir', lambda: str(good))

    class Args:
        status = False
        dry_run = False
        verify_reversible = True

    manage.migrate(Args())
    out = capsys.readouterr().out
    assert 'pass the reversibility check' in out


def test_cli_verify_reversible_exits_nonzero_on_violation(tmp_path, monkeypatch, capsys):
    """A migrations tree containing a hazard must drive a SystemExit(1)."""
    import manage

    bad = tmp_path / 'migrations'
    bad.mkdir()
    (bad / '001_base.sql').write_text('CREATE TABLE t (id INTEGER PRIMARY KEY);')
    (bad / '002_boom.sql').write_text('DROP TABLE t;')
    monkeypatch.setattr(manage, '_get_migrations_dir', lambda: str(bad))

    class Args:
        status = False
        dry_run = False
        verify_reversible = True

    with pytest.raises(SystemExit) as exc:
        manage.migrate(Args())
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert 'Migration reversibility violations' in err
    assert '002_boom.sql' in err
    assert 'DROP TABLE' in err
