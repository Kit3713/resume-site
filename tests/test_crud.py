"""
CRUD Helper Tests — Phase 29.2 (#56)

Covers ``app.services.crud.update_fields``, the partial-update helper
that wraps the UPDATE + activity-log INSERT in a single
``BEGIN IMMEDIATE`` transaction.

Migration smoke tests for the three services rewritten on top of the
helper (``update_service``, ``update_stat``, ``update_webhook``) live
in ``test_admin.py`` / ``test_webhooks.py``; this file exercises the
helper directly so a regression in its SQL splicing or transaction
handling surfaces close to the change.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.services.crud import update_fields

# ---------------------------------------------------------------------------
# DB fixture — reuse the conftest tmp DB path so the schema (including
# admin_activity_log) is already in place.
# ---------------------------------------------------------------------------


@pytest.fixture
def db(app):
    """Return an open sqlite3 Connection to the test DB."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    yield conn
    conn.close()


@pytest.fixture
def services_row(db):
    """Insert one services row and return its id.

    Uses the existing ``services`` table because its schema covers the
    column-types the helper has to bind (TEXT, INTEGER, with defaults).
    """
    cursor = db.execute(
        'INSERT INTO services (title, description, icon, sort_order) VALUES (?, ?, ?, ?)',
        ('Original', 'Original desc', '', 0),
    )
    db.commit()
    return cursor.lastrowid


_SERVICE_COLUMNS = {'title', 'description', 'icon', 'sort_order', 'visible'}


# ---------------------------------------------------------------------------
# Single-column update
# ---------------------------------------------------------------------------


def test_single_column_update_writes_value(db, services_row):
    """Updating one column reflects in the row."""
    rowcount = update_fields(
        db,
        'services',
        services_row,
        {'title': 'Renamed'},
        column_allowlist=_SERVICE_COLUMNS,
    )
    assert rowcount == 1
    row = db.execute('SELECT * FROM services WHERE id = ?', (services_row,)).fetchone()
    assert row['title'] == 'Renamed'
    # Other columns left alone.
    assert row['description'] == 'Original desc'


# ---------------------------------------------------------------------------
# Multi-column update
# ---------------------------------------------------------------------------


def test_multi_column_update_writes_all_values(db, services_row):
    """Updating two columns lands both."""
    update_fields(
        db,
        'services',
        services_row,
        {'title': 'Renamed', 'sort_order': 7},
        column_allowlist=_SERVICE_COLUMNS,
    )
    row = db.execute('SELECT * FROM services WHERE id = ?', (services_row,)).fetchone()
    assert row['title'] == 'Renamed'
    assert row['sort_order'] == 7
    # Untouched column unchanged.
    assert row['description'] == 'Original desc'


# ---------------------------------------------------------------------------
# Activity-log emission
# ---------------------------------------------------------------------------


def test_activity_log_row_inserted_on_success(db, services_row):
    """Activity event lands when caller passes one."""
    before = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']
    update_fields(
        db,
        'services',
        services_row,
        {'title': 'New'},
        column_allowlist=_SERVICE_COLUMNS,
        activity_event='Updated service',
        activity_category='services',
        activity_detail=f'id={services_row}',
    )
    after = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']
    assert after == before + 1
    row = db.execute('SELECT * FROM admin_activity_log ORDER BY id DESC LIMIT 1').fetchone()
    assert row['action'] == 'Updated service'
    assert row['category'] == 'services'
    assert row['detail'] == f'id={services_row}'


def test_activity_log_default_detail_includes_row_id(db, services_row):
    """When no detail is passed, the helper writes ``id=<row_id>``."""
    update_fields(
        db,
        'services',
        services_row,
        {'title': 'New'},
        column_allowlist=_SERVICE_COLUMNS,
        activity_event='Updated service',
        activity_category='services',
    )
    row = db.execute('SELECT * FROM admin_activity_log ORDER BY id DESC LIMIT 1').fetchone()
    assert row['detail'] == f'id={services_row}'


def test_no_activity_log_when_event_is_none(db, services_row):
    """Helper skips the INSERT when ``activity_event`` is None."""
    before = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']
    update_fields(
        db,
        'services',
        services_row,
        {'title': 'New'},
        column_allowlist=_SERVICE_COLUMNS,
    )
    after = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']
    assert after == before


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------


def test_unknown_column_raises_value_error(db, services_row):
    """Helper rejects keys outside the allowlist."""
    with pytest.raises(ValueError, match='unknown column'):
        update_fields(
            db,
            'services',
            services_row,
            {'no_such_column': 'whatever'},
            column_allowlist=_SERVICE_COLUMNS,
        )
    # Row untouched.
    row = db.execute('SELECT * FROM services WHERE id = ?', (services_row,)).fetchone()
    assert row['title'] == 'Original'


def test_empty_fields_dict_raises_value_error(db, services_row):
    """Empty dict is a caller bug — surface it loudly."""
    with pytest.raises(ValueError, match='empty fields'):
        update_fields(
            db,
            'services',
            services_row,
            {},
            column_allowlist=_SERVICE_COLUMNS,
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validation_callable_runs_before_update(db, services_row):
    """The validator sees the dict and can short-circuit the UPDATE."""
    seen = {}

    def _validator(fields):
        seen.update(fields)

    update_fields(
        db,
        'services',
        services_row,
        {'title': 'New'},
        column_allowlist=_SERVICE_COLUMNS,
        validate=_validator,
    )
    assert seen == {'title': 'New'}


def test_validation_failure_rolls_back_update(db, services_row):
    """A ValueError from the validator leaves the row + log untouched."""
    log_before = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']

    def _bad_validator(_fields):
        raise ValueError('bad input')

    with pytest.raises(ValueError, match='bad input'):
        update_fields(
            db,
            'services',
            services_row,
            {'title': 'Should not land'},
            column_allowlist=_SERVICE_COLUMNS,
            validate=_bad_validator,
            activity_event='Updated service',
            activity_category='services',
        )

    row = db.execute('SELECT * FROM services WHERE id = ?', (services_row,)).fetchone()
    assert row['title'] == 'Original'
    log_after = db.execute('SELECT COUNT(*) AS n FROM admin_activity_log').fetchone()['n']
    assert log_after == log_before


# ---------------------------------------------------------------------------
# Atomicity — concurrent BEGIN IMMEDIATE
# ---------------------------------------------------------------------------


def test_concurrent_update_one_wins_one_rolls_back(app, services_row):
    """Two concurrent BEGIN IMMEDIATE writers against the same row.

    SQLite's BEGIN IMMEDIATE pattern guarantees one writer holds the
    write lock at a time. The losing thread either waits for the
    busy_timeout and then proceeds, or its commit lands second; either
    way both updates eventually land without exception. The point of
    this test is the negative path: neither writer leaves the DB in a
    broken state, and the final row reflects exactly one set of values
    that came from one of the two callers (not a mishmash).
    """
    db_path = app.config['DATABASE_PATH']
    column_allowlist = _SERVICE_COLUMNS

    results = {'a': None, 'b': None}
    errors = []

    def _worker(letter, value):
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            update_fields(
                conn,
                'services',
                services_row,
                {'title': value},
                column_allowlist=column_allowlist,
            )
            results[letter] = value
        except Exception as exc:  # noqa: BLE001 — surface failures via list
            errors.append((letter, exc))
        finally:
            conn.close()

    threads = [
        threading.Thread(target=_worker, args=('a', 'Thread-A')),
        threading.Thread(target=_worker, args=('b', 'Thread-B')),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # Neither writer should have raised — busy_timeout (5 s) is plenty
    # for the second writer to acquire the lock once the first commits.
    assert errors == [], f'concurrent writers raised: {errors}'

    # Final row contains exactly one writer's value, not a partial /
    # interleaved write.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT title FROM services WHERE id = ?', (services_row,)).fetchone()
    finally:
        conn.close()
    assert row['title'] in {'Thread-A', 'Thread-B'}


def test_concurrent_writer_holding_lock_rolls_back_loser(app, services_row):
    """Open a long-held BEGIN IMMEDIATE on one connection, then try to
    grab the same lock from a second connection with a short busy_timeout.

    The second writer should raise OperationalError (database locked)
    and the helper should propagate it cleanly via rollback — leaving
    the DB un-poisoned for any subsequent transaction.

    Both connections live on the main thread so we don't run into
    sqlite3's per-thread-connection rule. The "concurrency" is
    simulated by holding a transaction open on the first connection
    while we issue from the second; SQLite's locking is at the DB
    level, not the thread level.
    """
    db_path = app.config['DATABASE_PATH']

    holder = sqlite3.connect(db_path, timeout=10)
    holder.row_factory = sqlite3.Row
    holder.execute('BEGIN IMMEDIATE')
    holder.execute('UPDATE services SET title = ? WHERE id = ?', ('Holder-A', services_row))

    contender = sqlite3.connect(db_path, timeout=10)
    contender.row_factory = sqlite3.Row
    # Short busy_timeout so the contender errors immediately rather
    # than blocking — we want to exercise the helper's rollback path.
    contender.execute('PRAGMA busy_timeout=200')

    try:
        with pytest.raises(sqlite3.OperationalError):
            update_fields(
                contender,
                'services',
                services_row,
                {'title': 'Contender-B'},
                column_allowlist=_SERVICE_COLUMNS,
            )
    finally:
        # Holder commits — releases the lock for any later assertions.
        holder.commit()
        holder.close()

    # Holder's value won.
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    try:
        row = check.execute('SELECT title FROM services WHERE id = ?', (services_row,)).fetchone()
    finally:
        check.close()
    assert row['title'] == 'Holder-A'

    # The contender's connection must still be usable for a subsequent
    # transaction — i.e. our rollback ran. A second update should
    # succeed without error.
    update_fields(
        contender,
        'services',
        services_row,
        {'title': 'After-rollback'},
        column_allowlist=_SERVICE_COLUMNS,
    )
    contender.close()


# ---------------------------------------------------------------------------
# Returned row count
# ---------------------------------------------------------------------------


def test_returns_zero_when_row_id_does_not_exist(db):
    """Updating a non-existent row returns 0; no exception."""
    rowcount = update_fields(
        db,
        'services',
        99999,
        {'title': 'Ghost'},
        column_allowlist=_SERVICE_COLUMNS,
    )
    assert rowcount == 0
