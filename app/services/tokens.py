"""
Review Token Validation Service

Manages the invite-only review system. The admin generates a unique,
URL-safe token for each trusted contact. The token encodes a URL like
`/review/<token>` where the contact can submit their testimonial.

Token lifecycle:
1. Admin generates token via admin panel or CLI (tagged as 'recommendation'
   or 'client_review').
2. Token is shared with the contact (via email, message, etc.).
3. Contact visits /review/<token> and submits their review.
4. Token is marked as used (single-use) and cannot be reused.

Tokens may optionally have an expiration date (expires_at field).
"""

from datetime import datetime, timezone


def validate_token(db, token_string):
    """Validate a review invitation token.

    Checks whether the token exists, hasn't been used, and hasn't expired.

    Args:
        db: The SQLite database connection.
        token_string: The URL-safe token string from the URL path.

    Returns:
        tuple: (token_row, error) where token_row is the sqlite3.Row
        (or None if not found) and error is one of None, 'invalid',
        'used', or 'expired'.
    """
    row = db.execute(
        'SELECT * FROM review_tokens WHERE token = ?', (token_string,)
    ).fetchone()

    if row is None:
        return None, 'invalid'

    if row['used']:
        return row, 'used'

    if row['expires_at']:
        try:
            expires = datetime.fromisoformat(row['expires_at'])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                return row, 'expired'
        except ValueError:
            pass  # Malformed date — treat as non-expiring

    return row, None
