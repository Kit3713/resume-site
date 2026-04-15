"""
Review Service (app/services/reviews.py)

Business logic for the reviews table. Handles the full review lifecycle:
approval, rejection, display tier updates, and listing by status.

Admin routes call these functions instead of writing SQL inline.
"""

from __future__ import annotations

import sqlite3

from app.exceptions import ValidationError

_VALID_STATUSES = ('pending', 'approved', 'rejected')
_VALID_TIERS = ('featured', 'standard', 'hidden')


def get_reviews_by_status(db: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    """Return all reviews with a given status, newest first.

    Args:
        db: Database connection.
        status: One of 'pending', 'approved', 'rejected'.

    Returns:
        List of sqlite3.Row objects.
    """
    if status not in _VALID_STATUSES:
        raise ValidationError(f'Invalid review status: {status!r}')
    return db.execute(
        'SELECT * FROM reviews WHERE status = ? ORDER BY created_at DESC',
        (status,),
    ).fetchall()


def approve_review(
    db: sqlite3.Connection, review_id: int, display_tier: str = 'standard'
) -> None:
    """Approve a review and set its display tier.

    Args:
        db: Database connection.
        review_id: The review's primary key.
        display_tier: Where to show the review ('featured', 'standard', 'hidden').
    """
    if display_tier not in _VALID_TIERS:
        display_tier = 'standard'
    db.execute(
        "UPDATE reviews SET status = 'approved', display_tier = ?, "
        "reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (display_tier, review_id),
    )
    db.commit()


def reject_review(db: sqlite3.Connection, review_id: int) -> None:
    """Reject a review. It will no longer appear on the public site."""
    db.execute(
        "UPDATE reviews SET status = 'rejected', "
        "reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (review_id,),
    )
    db.commit()


def update_review_tier(db: sqlite3.Connection, review_id: int, display_tier: str) -> None:
    """Update the display tier of an already-approved review.

    Args:
        db: Database connection.
        review_id: The review's primary key.
        display_tier: New tier ('featured', 'standard', 'hidden').
    """
    if display_tier not in _VALID_TIERS:
        display_tier = 'standard'
    db.execute(
        'UPDATE reviews SET display_tier = ? WHERE id = ?',
        (display_tier, review_id),
    )
    db.commit()


def count_pending(db: sqlite3.Connection) -> int:
    """Return the number of reviews awaiting approval."""
    row = db.execute("SELECT COUNT(*) as cnt FROM reviews WHERE status = 'pending'").fetchone()
    return row['cnt'] if row else 0
