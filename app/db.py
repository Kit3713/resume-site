"""
Database Connection Management (app/db.py)

Single source of truth for SQLite connection lifecycle.
All route files and services import get_db() from here.

Previously get_db() was duplicated in app/__init__.py and app/models.py.
This module consolidates both into one authoritative implementation.

PRAGMA strategy (Phase 12.1 audit)
----------------------------------
SQLite has two flavors of PRAGMA: persistent (stored in the DB file
header) and per-connection (re-applied every time you connect).

Persistent — set once at DB creation in schema.sql / migrations/001:
    journal_mode=WAL    Enables concurrent reads while a write is in
                        progress; survives across connections.

Per-connection — re-applied here on every connect:
    foreign_keys=ON     Enforces referential integrity. SQLite ships
                        with this OFF by default (compat reasons), so
                        every new connection MUST re-enable it.
    busy_timeout=5000   Block up to 5s on a write lock instead of
                        immediately raising SQLITE_BUSY. Important for
                        multi-worker Gunicorn deployments.

We don't pool connections: SQLite's per-process locking model means a
shared pool would be a contention bottleneck, not a win. Per-request
connect() is cheap (microseconds for a SQLite file open) and gives each
request a clean transaction state.

Usage:
    from app.db import get_db

    db = get_db()   # Returns the per-request connection from Flask's g object
"""

import sqlite3
import time

from flask import current_app, g

# PRAGMAs applied to every new connection. Listed here so the test suite
# can assert on the same source of truth the runtime uses.
_PER_CONNECTION_PRAGMAS = (
    ('foreign_keys', 'ON'),
    ('busy_timeout', '5000'),
)


def _apply_pragmas(conn):
    """Apply the per-connection PRAGMAs to a fresh sqlite3 connection.

    Extracted so non-Flask callers (CLI scripts, tests, ad-hoc maintenance
    scripts) can produce connections with the same configuration the app
    uses at request time.
    """
    for name, value in _PER_CONNECTION_PRAGMAS:
        # Both `name` and `value` come from the module-level constant tuple;
        # they're never caller-supplied. f-string is safe here.
        conn.execute(f'PRAGMA {name}={value}')


class _InstrumentedConnection:
    """Wraps a sqlite3.Connection to count queries and measure time.

    Stores running totals in ``flask.g.db_query_count`` and
    ``flask.g.db_query_time_ms`` so the request-logging hook and
    metrics system can access them.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        """Execute with timing instrumentation."""
        start = time.monotonic()
        result = self._conn.execute(sql, params)
        elapsed = (time.monotonic() - start) * 1000
        g.db_query_count = getattr(g, 'db_query_count', 0) + 1
        g.db_query_time_ms = getattr(g, 'db_query_time_ms', 0.0) + elapsed
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db():
    """Get or create a SQLite database connection for the current request.

    Connections are stored in Flask's `g` object and reused within a single
    request. See module docstring for the PRAGMA strategy. Wrapped in
    ``_InstrumentedConnection`` for query counting (Phase 18.3).

    Returns:
        _InstrumentedConnection: Instrumented database connection.
    """
    if 'db' not in g:
        conn = sqlite3.connect(current_app.config['DATABASE_PATH'])
        conn.row_factory = sqlite3.Row
        _apply_pragmas(conn)
        g._raw_db = conn
        g.db = _InstrumentedConnection(conn)
        g.db_query_count = 0
        g.db_query_time_ms = 0.0
    return g.db


def close_db(exception=None):
    """Close the database connection at the end of each request.

    Registered as a Flask teardown_appcontext handler by the app factory.
    The exception parameter is provided by Flask but not used here — we
    close the connection regardless of whether the request succeeded.
    """
    g.pop('db', None)
    raw = g.pop('_raw_db', None)
    if raw is not None:
        raw.close()
