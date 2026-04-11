from datetime import datetime


def validate_token(db, token_string):
    """Validate a review token. Returns (token_row, error) tuple.

    error is None if valid, 'used' if already used, 'expired' if past expiry,
    'invalid' if not found.
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
            if datetime.utcnow() > expires:
                return row, 'expired'
        except ValueError:
            pass

    return row, None
