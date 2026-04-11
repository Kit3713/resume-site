from flask_login import UserMixin


class AdminUser(UserMixin):
    """Single admin user backed by YAML config (not database)."""

    def __init__(self, username):
        self.id = username
        self.username = username


def get_db():
    """Get the database connection from Flask's g object."""
    from flask import g
    return g.db


def get_all_settings(db):
    """Return all settings as a dict."""
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return {row['key']: row['value'] for row in rows}


def get_setting(db, key, default=''):
    """Return a single setting value, or default if not found."""
    row = db.execute(
        'SELECT value FROM settings WHERE key = ?', (key,)
    ).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    """Insert or update a setting."""
    db.execute(
        'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, strftime(\'%Y-%m-%dT%H:%M:%SZ\', \'now\')) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
        (key, str(value)),
    )
    db.commit()
