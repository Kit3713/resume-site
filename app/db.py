"""
Database Connection Management (app/db.py)

Single source of truth for SQLite connection lifecycle.
All route files and services import get_db() from here.

Previously get_db() was duplicated in app/__init__.py and app/models.py.
This module consolidates both into one authoritative implementation.

Usage:
    from app.db import get_db

    db = get_db()   # Returns the per-request connection from Flask's g object
"""

import sqlite3

from flask import g, current_app


def get_db():
    """Get or create a SQLite database connection for the current request.

    Connections are stored in Flask's `g` object and reused within a single
    request. The connection is configured with:
    - Row factory: sqlite3.Row for dict-like column access (row['column_name']).
    - Foreign keys: Enforced via PRAGMA to maintain referential integrity.
    - Busy timeout: 5 seconds to handle concurrent writes from Gunicorn workers.

    Returns:
        sqlite3.Connection: The active database connection for this request.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE_PATH'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA busy_timeout=5000')
    return g.db


def close_db(exception=None):
    """Close the database connection at the end of each request.

    Registered as a Flask teardown_appcontext handler by the app factory.
    The exception parameter is provided by Flask but not used here — we
    close the connection regardless of whether the request succeeded.
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()
