"""PRAGMA audit (Phase 12.1).

Locks in the per-connection PRAGMA configuration declared in `app/db.py`.
If someone removes `foreign_keys=ON` or relaxes the `busy_timeout`, these
tests fail loudly. Also asserts that the database file itself was created
in WAL journal mode (a persistent setting from migration 001).
"""

import sqlite3

from app.db import _PER_CONNECTION_PRAGMAS, _apply_pragmas, get_db


def test_get_db_enables_foreign_keys(app):
    """Every connection from get_db() must have foreign_keys=ON."""
    with app.app_context():
        db = get_db()
        result = db.execute('PRAGMA foreign_keys').fetchone()[0]
        assert result == 1, 'foreign_keys must be ON to enforce FK constraints'


def test_get_db_sets_busy_timeout(app):
    """Every connection from get_db() must have busy_timeout >= 5000ms."""
    with app.app_context():
        db = get_db()
        result = db.execute('PRAGMA busy_timeout').fetchone()[0]
        assert result >= 5000, 'busy_timeout must absorb concurrent-write contention'


def test_database_file_uses_wal_journal_mode(app):
    """The DB file must have been created with journal_mode=WAL.

    WAL is a persistent property of the file (set in schema.sql /
    migration 001). Verify it survives across connections.
    """
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0].lower()
        assert mode == 'wal', f'expected WAL journal mode, got {mode!r}'
    finally:
        conn.close()


def test_apply_pragmas_helper_matches_get_db(app):
    """`_apply_pragmas` is the public hook for non-Flask callers — it must
    leave a fresh connection in exactly the same state as get_db()."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    try:
        _apply_pragmas(conn)
        for name, expected in _PER_CONNECTION_PRAGMAS:
            actual = conn.execute(f'PRAGMA {name}').fetchone()[0]
            # PRAGMA returns ints for boolean/numeric pragmas
            if expected == 'ON':
                assert actual == 1, f'{name} should be ON (1), got {actual}'
            else:
                assert int(actual) == int(expected), f'{name} should be {expected}, got {actual}'
    finally:
        conn.close()


def test_per_connection_pragma_inventory():
    """Pin the exact PRAGMA list. Adding a new one is fine — this just
    forces a deliberate test update so the audit stays accurate."""
    expected = {('foreign_keys', 'ON'), ('busy_timeout', '5000')}
    assert set(_PER_CONNECTION_PRAGMAS) == expected
