"""
Activity Log Service (app/services/activity_log.py)

Records admin panel actions for the dashboard audit trail. Each entry
captures what happened, what category of content was affected, and a
brief detail string. The log is append-only and displayed on the admin
dashboard as a recent activity feed.

Append-only enforcement (#105). Migration 013 installs a BEFORE DELETE
trigger and a BEFORE UPDATE trigger on ``admin_activity_log`` that
``RAISE(ABORT)``. Any mutation outside :func:`purge_old_entries`
raises ``sqlite3.IntegrityError`` — documentation alone wasn't
enough; a stray ``DELETE FROM admin_activity_log`` would otherwise
erase the trail. ``purge_old_entries`` is the single, documented
gate for the retention path.
"""

from __future__ import annotations

import sqlite3

# Recreated verbatim by ``purge_old_entries`` after the safe-purge
# DELETE. Mirrors migration 013's CREATE TRIGGER body so the
# operator-level invariant holds across the function call. Keep in
# sync with ``migrations/013_admin_activity_log_append_only.sql``.
_NO_DELETE_TRIGGER_SQL = (
    'CREATE TRIGGER IF NOT EXISTS admin_activity_log_no_delete '
    'BEFORE DELETE ON admin_activity_log '
    'BEGIN '
    "SELECT RAISE(ABORT, 'admin_activity_log is append-only; safe-purge required'); "
    'END'
)


def log_action(
    db: sqlite3.Connection,
    action: str,
    category: str = '',
    detail: str = '',
    admin_user: str = 'admin',
) -> None:
    """Record an admin action to the activity log.

    Args:
        db: Database connection.
        action: Short verb phrase (e.g., 'Published post', 'Deleted photo').
        category: Content type affected (e.g., 'blog', 'photos', 'settings').
        detail: Additional context (e.g., post title, filename).
        admin_user: The admin username (for future multi-user support).
    """
    db.execute(
        'INSERT INTO admin_activity_log (action, category, detail, admin_user) VALUES (?, ?, ?, ?)',
        (action, category, detail, admin_user),
    )
    db.commit()


def get_recent_activity(db: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """Return the most recent activity log entries for the dashboard."""
    return db.execute(
        'SELECT * FROM admin_activity_log ORDER BY created_at DESC LIMIT ?',
        (limit,),
    ).fetchall()


def purge_old_entries(db: sqlite3.Connection, days: int = 90) -> int:
    """SAFE-PURGE: retain only the last ``days`` of audit log.

    This is the documented, single-purpose gate for shrinking the
    audit log. Migration 013 installs an append-only BEFORE DELETE
    trigger on ``admin_activity_log`` so direct ``DELETE`` from
    anywhere else fails with ``sqlite3.IntegrityError``. To honour
    the retention window, this function temporarily drops the
    trigger, runs the bounded DELETE, then recreates the trigger
    inside a ``finally`` so an exception during the DELETE does not
    leave the table unguarded.

    Args:
        db: Database connection.
        days: Retention window. Rows older than ``now - days`` are
            deleted. Default 90.

    Returns:
        int: Number of entries deleted.
    """
    db.execute('DROP TRIGGER IF EXISTS admin_activity_log_no_delete')
    try:
        cursor = db.execute(
            'DELETE FROM admin_activity_log '
            "WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
            (f'-{days} days',),
        )
        deleted = cursor.rowcount
    finally:
        # Re-create the trigger even when the DELETE raised; the
        # invariant is "the table is guarded after this call returns".
        db.execute(_NO_DELETE_TRIGGER_SQL)
    db.commit()
    return deleted
