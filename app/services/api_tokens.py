"""
API Token Authentication Service — Phase 13.4

Token-based authentication for the forthcoming REST API (Phase 16).
Tokens are bearer credentials: anyone holding the raw string can use it,
so the surface area is kept small.

Storage model
-------------
The raw token is a 32-byte ``secrets.token_urlsafe`` value (43 base64url
characters). Only its SHA-256 hash ever touches disk — the raw value is
printed once at generation time and never persisted. An attacker with
read-only database access therefore cannot recover any usable token.

Scope semantics
---------------
Scopes are EXPLICIT — ``write`` does NOT imply ``read``. A decorator of
``@require_api_token(scope='read')`` accepts only tokens whose scope
list explicitly contains ``read``. Rationale: avoid the common foot-gun
where a write-only token unintentionally becomes usable for reads the
holder shouldn't see. Issue ``read,write`` explicitly when both are
needed.

Dependencies
------------
Dependency-free (stdlib only) at the service layer. The decorator
imports from Flask lazily so this module can be imported from the CLI
without bringing up an application context.

Events
------
Callers are responsible for emitting ``Events.API_TOKEN_CREATED`` after
a successful :func:`generate_token` or :func:`rotate_token`. This keeps
the service layer pure (no Flask / no events) and matches how the
``backups`` service hands off emission to its CLI + admin callers.
"""

from __future__ import annotations

import functools
import hashlib
import re
import secrets
from collections import namedtuple
from datetime import UTC, datetime, timedelta

# A token with one of these values is valid; anything else is rejected
# at generate/rotate time. Ordered so `validate_scope` error messages
# read naturally.
VALID_SCOPES = ('read', 'write', 'admin')

# ``GeneratedToken.raw`` is the ONLY place a raw token exists in Python
# process memory. The caller prints / flashes it once and discards.
GeneratedToken = namedtuple(
    'GeneratedToken',
    ('id', 'raw', 'name', 'scope', 'expires_at'),
)

# Returned by :func:`list_tokens` — deliberately omits any hash material.
TokenRecord = namedtuple(
    'TokenRecord',
    (
        'id',
        'name',
        'scope',
        'created_at',
        'expires_at',
        'last_used_at',
        'revoked',
        'created_by',
    ),
)

# Attached to ``flask.g.api_token`` on successful auth.
VerifiedToken = namedtuple('VerifiedToken', ('id', 'name', 'scope_list'))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Base for all service-layer token errors."""


class InvalidScopeError(TokenError):
    """Raised when a scope string contains an unknown value."""


class TokenNotFoundError(TokenError):
    """Raised when a lookup-by-name or lookup-by-id finds no match."""


class AuthError(TokenError):
    """Raised by :func:`verify_token` on any authentication failure.

    ``reason`` is a short machine-readable tag; ``http_status`` tells the
    decorator whether to return 401 (missing / invalid / expired /
    revoked) or 403 (insufficient_scope).
    """

    def __init__(self, reason, http_status):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now(now):
    return now if now is not None else datetime.now(UTC)


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _hash(raw):
    """SHA-256 hex digest of the raw token bytes."""
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _parse_scope(raw):
    """Normalise ``raw`` into a sorted-unique list of valid scope names.

    Accepts a comma-separated string with optional whitespace. Raises
    :class:`InvalidScopeError` on any unknown value. An empty result is
    also rejected — a token with no scope is not useful.
    """
    if raw is None:
        raise InvalidScopeError('scope is required')
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if not parts:
        raise InvalidScopeError('scope is required')
    unknown = [p for p in parts if p not in VALID_SCOPES]
    if unknown:
        raise InvalidScopeError(
            f'unknown scope(s): {", ".join(sorted(unknown))} (valid: {", ".join(VALID_SCOPES)})'
        )
    # De-dup but preserve original input order for display.
    seen = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return seen


def _scope_list(raw):
    """Return the scope list of an already-stored token (permissive)."""
    return [p.strip() for p in (raw or '').split(',') if p.strip()]


_DURATION_RE = re.compile(r'^(\d+)\s*([hd])$', re.IGNORECASE)


def parse_expires(raw, *, now=None):
    """Translate a CLI/form ``--expires`` value into an ISO-8601 UTC string.

    Accepts:
        * ``None`` / empty string / ``never`` → ``None`` (no expiry)
        * ``<N>h`` → N hours from now (e.g. ``24h``)
        * ``<N>d`` → N days from now (e.g. ``7d``, ``90d``)
        * ISO-8601 date or datetime (e.g. ``2026-07-01`` or
          ``2026-07-01T00:00:00+00:00``) → that timestamp in UTC

    Raises :class:`ValueError` for anything else.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw.lower() == 'never':
        return None

    match = _DURATION_RE.match(raw)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError(f'duration must be positive: {raw!r}')
        unit = match.group(2).lower()
        delta = timedelta(hours=amount) if unit == 'h' else timedelta(days=amount)
        return _iso(_now(now) + delta)

    # Accept bare date (YYYY-MM-DD) or full ISO-8601.
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f'invalid --expires value: {raw!r} (expected Nd / Nh / never / ISO-8601 date)'
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return _iso(parsed)


def _is_expired(expires_at, now_dt):
    """Return True if ``expires_at`` (ISO-8601 or None) is in the past."""
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError:
        # Malformed → treat as non-expiring (matches tokens.py behaviour).
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return now_dt >= parsed


# ---------------------------------------------------------------------------
# Service API
# ---------------------------------------------------------------------------


def generate_token(db, *, name, scope, expires_at=None, created_by='admin'):
    """Create a new API token. Return :class:`GeneratedToken` including the raw value.

    The raw value is returned ONCE — the caller is expected to display
    it to the operator and then discard. Only the SHA-256 hash is
    persisted.

    Args:
        db: Open sqlite3 connection.
        name: Human label (non-empty).
        scope: Comma-separated scope string (e.g. ``'read,write'``).
        expires_at: ISO-8601 UTC timestamp or ``None`` for no expiry.
            Use :func:`parse_expires` to translate CLI-style inputs.
        created_by: Admin username at creation time.

    Raises:
        InvalidScopeError: ``scope`` contains an unknown value or is empty.
        ValueError: ``name`` is empty.
    """
    if not name or not name.strip():
        raise ValueError('name is required')
    scope_list = _parse_scope(scope)
    scope_str = ','.join(scope_list)

    raw = secrets.token_urlsafe(32)
    token_hash = _hash(raw)

    cursor = db.execute(
        'INSERT INTO api_tokens (token_hash, name, scope, expires_at, created_by) '
        'VALUES (?, ?, ?, ?, ?)',
        (token_hash, name.strip(), scope_str, expires_at, created_by),
    )
    db.commit()
    return GeneratedToken(
        id=cursor.lastrowid,
        raw=raw,
        name=name.strip(),
        scope=scope_str,
        expires_at=expires_at,
    )


def verify_token(db, authorization_header, required_scope, *, now=None):
    """Validate a ``Authorization: Bearer <token>`` header.

    On success, UPDATE ``last_used_at`` and return :class:`VerifiedToken`.

    Args:
        db: Open sqlite3 connection.
        authorization_header: Raw ``Authorization`` header value, or ``''``.
        required_scope: The single scope the endpoint demands.
        now: Optional UTC datetime for deterministic testing.

    Raises:
        AuthError: Every failure path — ``.reason`` tells the decorator
            which wire message to use; ``.http_status`` is 401 for any
            authentication failure and 403 for insufficient_scope.
    """
    if not authorization_header:
        raise AuthError('missing', 401)

    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1].strip():
        raise AuthError('malformed', 401)

    raw_token = parts[1].strip()
    token_hash = _hash(raw_token)

    row = db.execute(
        'SELECT id, name, scope, expires_at, revoked, token_hash FROM api_tokens '
        'WHERE token_hash = ?',
        (token_hash,),
    ).fetchone()

    if row is None:
        raise AuthError('invalid', 401)

    # Belt-and-braces constant-time check against the stored hash. The
    # SELECT already matched on equality — this simply keeps the
    # app-layer comparison path timing-uniform regardless of SQLite's
    # internal index-walk characteristics.
    stored_hash = row['token_hash'] if isinstance(row, dict) or hasattr(row, 'keys') else row[5]
    if not secrets.compare_digest(stored_hash, token_hash):
        raise AuthError('invalid', 401)

    revoked = row['revoked'] if hasattr(row, 'keys') else row[4]
    if revoked:
        raise AuthError('revoked', 401)

    expires_at = row['expires_at'] if hasattr(row, 'keys') else row[3]
    if _is_expired(expires_at, _now(now)):
        raise AuthError('expired', 401)

    scope_str = row['scope'] if hasattr(row, 'keys') else row[2]
    scope_list = _scope_list(scope_str)
    if required_scope not in scope_list:
        raise AuthError('insufficient_scope', 403)

    token_id = row['id'] if hasattr(row, 'keys') else row[0]
    token_name = row['name'] if hasattr(row, 'keys') else row[1]

    # Best-effort update of last_used_at. Kept in its own statement
    # after the SELECT so a concurrent verify request serialises on
    # SQLite's default write lock rather than blocking the earlier
    # read.
    db.execute(
        'UPDATE api_tokens SET last_used_at = ? WHERE id = ?',
        (_iso(_now(now)), token_id),
    )
    db.commit()

    return VerifiedToken(id=token_id, name=token_name, scope_list=scope_list)


def rotate_token(db, *, name, created_by='admin', now=None):
    """Generate a fresh token inheriting scope + expires_at from the newest
    active match on ``name``, then mark the old row revoked.

    Raises:
        TokenNotFoundError: No active (revoked=0) token matches ``name``.
    """
    row = db.execute(
        'SELECT id, scope, expires_at FROM api_tokens '
        'WHERE name = ? AND revoked = 0 '
        'ORDER BY created_at DESC LIMIT 1',
        (name,),
    ).fetchone()
    if row is None:
        raise TokenNotFoundError(f'no active token named {name!r}')

    old_id = row['id'] if hasattr(row, 'keys') else row[0]
    scope_str = row['scope'] if hasattr(row, 'keys') else row[1]
    expires_at = row['expires_at'] if hasattr(row, 'keys') else row[2]

    raw = secrets.token_urlsafe(32)
    token_hash = _hash(raw)
    cursor = db.execute(
        'INSERT INTO api_tokens (token_hash, name, scope, expires_at, created_by) '
        'VALUES (?, ?, ?, ?, ?)',
        (token_hash, name, scope_str, expires_at, created_by),
    )
    db.execute(
        'UPDATE api_tokens SET revoked = 1 WHERE id = ?',
        (old_id,),
    )
    db.commit()

    _ = now  # parameter reserved for test determinism / future use
    return GeneratedToken(
        id=cursor.lastrowid,
        raw=raw,
        name=name,
        scope=scope_str,
        expires_at=expires_at,
    )


def revoke_token(db, token_id):
    """Mark ``token_id`` as revoked. Idempotent — returns True if a row changed."""
    cursor = db.execute(
        'UPDATE api_tokens SET revoked = 1 WHERE id = ? AND revoked = 0',
        (token_id,),
    )
    db.commit()
    return cursor.rowcount > 0


def list_tokens(db, *, include_revoked=True):
    """Return all token records ordered by created_at DESC.

    The raw hashes are deliberately excluded — callers only ever need
    the metadata. Pass ``include_revoked=False`` to filter the soft-
    deleted rows out.
    """
    query = (
        'SELECT id, name, scope, created_at, expires_at, last_used_at, '
        'revoked, created_by FROM api_tokens'
    )
    params: tuple = ()
    if not include_revoked:
        query += ' WHERE revoked = 0'
    # Tiebreak on id DESC because created_at has 1-second resolution
    # and tokens created in the same second would otherwise sort
    # arbitrarily.
    query += ' ORDER BY created_at DESC, id DESC'
    rows = db.execute(query, params).fetchall()
    return [
        TokenRecord(
            id=row['id'] if hasattr(row, 'keys') else row[0],
            name=row['name'] if hasattr(row, 'keys') else row[1],
            scope=row['scope'] if hasattr(row, 'keys') else row[2],
            created_at=row['created_at'] if hasattr(row, 'keys') else row[3],
            expires_at=row['expires_at'] if hasattr(row, 'keys') else row[4],
            last_used_at=row['last_used_at'] if hasattr(row, 'keys') else row[5],
            revoked=bool(row['revoked'] if hasattr(row, 'keys') else row[6]),
            created_by=row['created_by'] if hasattr(row, 'keys') else row[7],
        )
        for row in rows
    ]


def get_token(db, token_id):
    """Return a single :class:`TokenRecord` by id, or ``None``."""
    row = db.execute(
        'SELECT id, name, scope, created_at, expires_at, last_used_at, '
        'revoked, created_by FROM api_tokens WHERE id = ?',
        (token_id,),
    ).fetchone()
    if row is None:
        return None
    return TokenRecord(
        id=row['id'] if hasattr(row, 'keys') else row[0],
        name=row['name'] if hasattr(row, 'keys') else row[1],
        scope=row['scope'] if hasattr(row, 'keys') else row[2],
        created_at=row['created_at'] if hasattr(row, 'keys') else row[3],
        expires_at=row['expires_at'] if hasattr(row, 'keys') else row[4],
        last_used_at=row['last_used_at'] if hasattr(row, 'keys') else row[5],
        revoked=bool(row['revoked'] if hasattr(row, 'keys') else row[6]),
        created_by=row['created_by'] if hasattr(row, 'keys') else row[7],
    )


def purge_expired(db, *, grace_days=30, now=None):
    """Delete revoked rows older than ``grace_days``, plus expired rows
    whose ``expires_at`` is older than ``grace_days``.

    Returns the number of rows removed. Intended for a future admin
    housekeeping command; not wired into any schedule in Phase 13.4.
    """
    if grace_days <= 0:
        return 0
    cutoff = _iso(_now(now) - timedelta(days=grace_days))
    cursor = db.execute(
        'DELETE FROM api_tokens '
        'WHERE (revoked = 1 AND created_at < ?) '
        'OR (expires_at IS NOT NULL AND expires_at < ?)',
        (cutoff, cutoff),
    )
    db.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Decorator (for Phase 16 REST API routes)
# ---------------------------------------------------------------------------


def require_api_token(scope):
    """Decorator factory for API routes.

    Usage (Phase 16)::

        @api_bp.route('/api/v1/blog')
        @require_api_token('read')
        def list_posts():
            ...

    Behaviour:

    * Missing / malformed ``Authorization: Bearer <token>`` → 401 with
      ``WWW-Authenticate: Bearer`` header. JSON body
      ``{"error": "missing"}`` or ``{"error": "malformed"}``.
    * Token hash not found → 401 ``{"error": "invalid"}``.
    * Revoked token → 401 ``{"error": "revoked"}``.
    * Expired token → 401 ``{"error": "expired"}``.
    * Scope mismatch → 403 ``{"error": "insufficient_scope"}``.
    * On success, ``flask.g.api_token`` is populated with a
      :class:`VerifiedToken` so downstream handlers can inspect the
      scope list or correlate via ``token_id``.

    The decorator does NOT accept tokens presented as query
    parameters — only the Authorization header — to avoid token
    leakage through access logs and browser history.
    """

    def _decorator(view):
        @functools.wraps(view)
        def _wrapped(*args, **kwargs):
            from flask import g, jsonify, request

            from app.db import get_db

            auth_hdr = request.headers.get('Authorization', '')
            try:
                verified = verify_token(get_db(), auth_hdr, scope)
            except AuthError as exc:
                response = jsonify({'error': exc.reason})
                response.status_code = exc.http_status
                if exc.http_status == 401:
                    response.headers['WWW-Authenticate'] = 'Bearer'
                return response
            g.api_token = verified
            return view(*args, **kwargs)

        return _wrapped

    return _decorator


# ---------------------------------------------------------------------------
# Rate-limit callables (for Phase 16 REST API routes)
# ---------------------------------------------------------------------------


def _rate_for(setting_key, fallback):
    """Build a Flask-Limiter-compatible callable that reads the current
    per-scope rate limit from the settings table.

    Returns a string of the form ``"<N> per minute"``. Changes to the
    setting propagate within ``DEFAULT_SETTINGS_TTL`` seconds (30s)
    because the callable reads through :func:`settings_svc.get_all_cached`.
    """

    def _cb():
        from flask import current_app

        from app.db import get_db
        from app.services.settings_svc import get_all_cached

        db = get_db()
        settings = get_all_cached(db, current_app.config['DATABASE_PATH'])
        raw = str(settings.get(setting_key, fallback)).strip()
        if not raw:
            raw = fallback
        if 'per' in raw:
            return raw
        return f'{raw} per minute'

    return _cb


rate_limit_read = _rate_for('api_rate_limit_read', '60')
rate_limit_write = _rate_for('api_rate_limit_write', '30')
rate_limit_admin = _rate_for('api_rate_limit_admin', '10')
