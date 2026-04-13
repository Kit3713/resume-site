"""
Stats Service (app/services/stats.py)

Business logic for the stats table (animated counter cards on the landing page).
Admin routes call these functions instead of writing SQL inline.
"""

from app.exceptions import ValidationError


def get_all_stats(db):
    """Return all stat counters ordered by sort_order."""
    return db.execute('SELECT * FROM stats ORDER BY sort_order').fetchall()


def add_stat(db, label, value, suffix='', sort_order=0):
    """Insert a new stat counter.

    Args:
        db: Database connection.
        label: Display label (e.g., 'Projects').
        value: Integer value.
        suffix: Optional suffix (e.g., '+', '%', 'k').
        sort_order: Display order (lower = earlier).
    """
    if not label:
        raise ValidationError('Stat label cannot be empty.')
    db.execute(
        'INSERT INTO stats (label, value, suffix, sort_order) VALUES (?, ?, ?, ?)',
        (label.strip(), int(value), suffix, int(sort_order)),
    )
    db.commit()


def update_stat(db, stat_id, label, value, suffix='', sort_order=0, visible=True):
    """Update an existing stat counter.

    Args:
        db: Database connection.
        stat_id: The stat's primary key.
        label: Display label.
        value: Integer value.
        suffix: Optional suffix.
        sort_order: Display order.
        visible: Whether to show on the public site.
    """
    db.execute(
        'UPDATE stats SET label = ?, value = ?, suffix = ?, sort_order = ?, visible = ? WHERE id = ?',
        (label.strip(), int(value), suffix, int(sort_order), 1 if visible else 0, stat_id),
    )
    db.commit()


def delete_stat(db, stat_id):
    """Delete a stat counter by ID."""
    db.execute('DELETE FROM stats WHERE id = ?', (stat_id,))
    db.commit()
