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

from datetime import datetime


def validate_token(db, token_string):
    """Validate a review invitation token.

    Checks whether the token exists, hasn't been used, and hasn't expired.
    Returns a tuple of (token_row, error_string) where error is None if
    the token is valid.

    Args:
        db: The SQLite database connection.
        token_string: The URL-safe token string from the URL path.

    Returns:
        tuple: (token_row, error) where:
            - token_row is the sqlite3.Row object (or None if not found)
            - error is None (valid), 'invalid' (not found), 'used' (already
              submitted), or 'expired' (past expiration date)
    """
    row = db.execute(
        'SELECT * FROM review_tokens WHERE token = ?', (token_string,)
    ).fetchone()

    if row is None:
        return None, 'invalid'

    if row['used']:
        return row, 'used'

    # Check optional expiration date
    if row['expires_at']:
        try:
            expires = datetime.fromisoformat(row['expires_at'])
            if datetime.utcnow() > expires:
                return row, 'expired'
        except ValueError:
            # Malformed date — treat as non-expiring rather than blocking
            pass

    return row, None
