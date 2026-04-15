"""
Activity Log Service (app/services/activity_log.py)

Records admin panel actions for the dashboard audit trail. Each entry
captures what happened, what category of content was affected, and a
brief detail string. The log is append-only and displayed on the admin
dashboard as a recent activity feed.
"""

from __future__ import annotations

import sqlite3


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
    """Delete activity log entries older than the specified number of days.

    Returns:
        int: Number of entries deleted.
    """
    cursor = db.execute(
        'DELETE FROM admin_activity_log '
        "WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (f'-{days} days',),
    )
    db.commit()
    return cursor.rowcount
