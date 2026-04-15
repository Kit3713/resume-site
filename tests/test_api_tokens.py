"""
API Token Service Tests — Phase 13.4

Covers :mod:`app.services.api_tokens`:

* Generation: raw returned exactly once, only SHA-256 hash persisted,
  scope validated, bad scope / empty name rejected.
* Verification: missing / malformed / invalid / revoked / expired /
  insufficient_scope all map to the right :class:`AuthError`; the
  success path updates ``last_used_at``.
* Rotation: new token created, old token marked revoked, scope and
  expiry inherited.
* Revocation: soft-delete flips the bit; idempotent on re-call.
* Expiry parsing: Nd / Nh / never / bare date / full ISO-8601 all
  translate to the expected ISO-8601 UTC string (or None).
* Purge: only rows past the grace window are removed.
* Decorator: 401 on any auth failure (with WWW-Authenticate: Bearer);
  403 on scope mismatch; ``flask.g.api_token`` populated on success;
  tokens presented as query strings are rejected.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.services.api_tokens import (
    AuthError,
    GeneratedToken,
    InvalidScopeError,
    TokenNotFoundError,
    VerifiedToken,
    generate_token,
    get_token,
    list_tokens,
    parse_expires,
    purge_expired,
    revoke_token,
    rotate_token,
    verify_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def token_db(tmp_path):
    """An isolated sqlite3 connection with just the api_tokens table."""
    path = tmp_path / 'api_tokens.db'
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE api_tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash    TEXT    NOT NULL UNIQUE,
            name          TEXT    NOT NULL,
            scope         TEXT    NOT NULL DEFAULT 'read',
            created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at    TEXT,
            last_used_at  TEXT,
            revoked       INTEGER NOT NULL DEFAULT 0,
            created_by    TEXT    NOT NULL DEFAULT 'admin'
        );
        CREATE INDEX idx_api_tokens_hash ON api_tokens(token_hash);
        CREATE INDEX idx_api_tokens_name ON api_tokens(name);
        """
    )
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# generate_token
# ---------------------------------------------------------------------------


def test_generate_token_returns_raw_value_once(token_db):
    result = generate_token(token_db, name='CI', scope='read')

    assert isinstance(result, GeneratedToken)
    assert result.raw
    # 32 random bytes → 43 URL-safe base64 chars (no padding).
    assert len(result.raw) >= 40
    assert result.name == 'CI'
    assert result.scope == 'read'
    assert result.expires_at is None


def test_generate_token_persists_only_the_hash(token_db):
    result = generate_token(token_db, name='CI', scope='read')

    row = token_db.execute(
        'SELECT token_hash, name, scope FROM api_tokens WHERE id = ?',
        (result.id,),
    ).fetchone()
    expected_hash = hashlib.sha256(result.raw.encode()).hexdigest()
    assert row['token_hash'] == expected_hash
    # Sanity: the raw value is nowhere in the stored columns.
    assert result.raw not in row['token_hash']
    assert result.raw not in row['name']
    assert result.raw not in row['scope']


def test_generate_token_accepts_comma_separated_scope(token_db):
    result = generate_token(token_db, name='CI', scope='read, write')
    assert result.scope == 'read,write'


def test_generate_token_dedups_scope(token_db):
    result = generate_token(token_db, name='CI', scope='read,read,write')
    assert result.scope == 'read,write'


def test_generate_token_rejects_unknown_scope(token_db):
    with pytest.raises(InvalidScopeError):
        generate_token(token_db, name='CI', scope='read,nope')


def test_generate_token_rejects_empty_scope(token_db):
    with pytest.raises(InvalidScopeError):
        generate_token(token_db, name='CI', scope='')


def test_generate_token_rejects_empty_name(token_db):
    with pytest.raises(ValueError, match='name is required'):
        generate_token(token_db, name='   ', scope='read')


def test_generate_token_stores_expires_at(token_db):
    result = generate_token(
        token_db,
        name='CI',
        scope='read',
        expires_at='2027-01-01T00:00:00Z',
    )
    assert result.expires_at == '2027-01-01T00:00:00Z'
    row = token_db.execute(
        'SELECT expires_at FROM api_tokens WHERE id = ?',
        (result.id,),
    ).fetchone()
    assert row['expires_at'] == '2027-01-01T00:00:00Z'


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


def test_verify_token_missing_header_raises_401(token_db):
    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, '', 'read')
    assert exc_info.value.reason == 'missing'
    assert exc_info.value.http_status == 401


def test_verify_token_malformed_header_raises_401(token_db):
    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, 'NotBearer foo', 'read')
    assert exc_info.value.reason == 'malformed'

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, 'Bearer', 'read')
    assert exc_info.value.reason == 'malformed'

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, 'Bearer    ', 'read')
    assert exc_info.value.reason == 'malformed'


def test_verify_token_unknown_hash_raises_401(token_db):
    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, 'Bearer nosuch', 'read')
    assert exc_info.value.reason == 'invalid'
    assert exc_info.value.http_status == 401


def test_verify_token_revoked_raises_401(token_db):
    result = generate_token(token_db, name='CI', scope='read')
    revoke_token(token_db, result.id)

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, f'Bearer {result.raw}', 'read')
    assert exc_info.value.reason == 'revoked'
    assert exc_info.value.http_status == 401


def test_verify_token_expired_raises_401(token_db):
    past = (datetime.now(UTC) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    result = generate_token(token_db, name='CI', scope='read', expires_at=past)

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, f'Bearer {result.raw}', 'read')
    assert exc_info.value.reason == 'expired'


def test_verify_token_insufficient_scope_raises_403(token_db):
    result = generate_token(token_db, name='CI', scope='read')

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, f'Bearer {result.raw}', 'write')
    assert exc_info.value.reason == 'insufficient_scope'
    assert exc_info.value.http_status == 403


def test_verify_token_success_updates_last_used_at(token_db):
    result = generate_token(token_db, name='CI', scope='read,write')

    verified = verify_token(token_db, f'Bearer {result.raw}', 'read')

    assert isinstance(verified, VerifiedToken)
    assert verified.id == result.id
    assert verified.name == 'CI'
    assert sorted(verified.scope_list) == ['read', 'write']

    row = token_db.execute(
        'SELECT last_used_at FROM api_tokens WHERE id = ?',
        (result.id,),
    ).fetchone()
    assert row['last_used_at'] is not None


def test_verify_token_does_not_update_last_used_on_failure(token_db):
    result = generate_token(token_db, name='CI', scope='read')
    revoke_token(token_db, result.id)

    with pytest.raises(AuthError):
        verify_token(token_db, f'Bearer {result.raw}', 'read')

    row = token_db.execute(
        'SELECT last_used_at FROM api_tokens WHERE id = ?',
        (result.id,),
    ).fetchone()
    assert row['last_used_at'] is None


def test_verify_token_write_does_not_imply_read(token_db):
    """Explicit scope semantics: write-only tokens cannot read."""
    result = generate_token(token_db, name='CI', scope='write')

    with pytest.raises(AuthError) as exc_info:
        verify_token(token_db, f'Bearer {result.raw}', 'read')
    assert exc_info.value.reason == 'insufficient_scope'


# ---------------------------------------------------------------------------
# rotate_token
# ---------------------------------------------------------------------------


def test_rotate_token_revokes_old_and_inherits(token_db):
    original = generate_token(
        token_db,
        name='integration',
        scope='read,write',
        expires_at='2030-01-01T00:00:00Z',
    )

    rotated = rotate_token(token_db, name='integration')

    assert rotated.id != original.id
    assert rotated.raw != original.raw
    assert rotated.scope == 'read,write'
    assert rotated.expires_at == '2030-01-01T00:00:00Z'

    # Old row is revoked.
    old = token_db.execute('SELECT revoked FROM api_tokens WHERE id = ?', (original.id,)).fetchone()
    assert old['revoked'] == 1

    # New row is active.
    new = token_db.execute('SELECT revoked FROM api_tokens WHERE id = ?', (rotated.id,)).fetchone()
    assert new['revoked'] == 0


def test_rotate_token_unknown_name_raises(token_db):
    with pytest.raises(TokenNotFoundError):
        rotate_token(token_db, name='ghost')


def test_rotate_token_ignores_already_revoked(token_db):
    result = generate_token(token_db, name='integration', scope='read')
    revoke_token(token_db, result.id)

    with pytest.raises(TokenNotFoundError):
        rotate_token(token_db, name='integration')


# ---------------------------------------------------------------------------
# revoke_token / list_tokens / get_token / purge_expired
# ---------------------------------------------------------------------------


def test_revoke_token_idempotent(token_db):
    result = generate_token(token_db, name='CI', scope='read')

    assert revoke_token(token_db, result.id) is True
    # Second call sees revoked=1 already → nothing to update.
    assert revoke_token(token_db, result.id) is False


def test_revoke_token_missing_id_returns_false(token_db):
    assert revoke_token(token_db, 999) is False


def test_list_tokens_returns_records_without_hashes(token_db):
    a = generate_token(token_db, name='A', scope='read')
    b = generate_token(token_db, name='B', scope='read,write')

    records = list_tokens(token_db)

    assert len(records) == 2
    # newest first
    assert records[0].id == b.id
    assert records[1].id == a.id
    # No hash attribute on TokenRecord.
    assert not hasattr(records[0], 'token_hash')


def test_list_tokens_can_exclude_revoked(token_db):
    a = generate_token(token_db, name='A', scope='read')
    b = generate_token(token_db, name='B', scope='read')
    revoke_token(token_db, a.id)

    active = list_tokens(token_db, include_revoked=False)
    assert [r.id for r in active] == [b.id]


def test_get_token_returns_record_or_none(token_db):
    result = generate_token(token_db, name='CI', scope='read')
    record = get_token(token_db, result.id)
    assert record is not None
    assert record.name == 'CI'
    assert get_token(token_db, 999) is None


def test_purge_expired_removes_old_revoked_and_expired(token_db):
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    # Row 1: revoked long ago → removed.
    stale = generate_token(token_db, name='stale', scope='read')
    token_db.execute(
        'UPDATE api_tokens SET revoked = 1, created_at = ? WHERE id = ?',
        ('2025-01-01T00:00:00Z', stale.id),
    )
    # Row 2: expired long ago → removed.
    expired = generate_token(
        token_db, name='expired', scope='read', expires_at='2025-01-01T00:00:00Z'
    )
    # Row 3: fresh and active → retained.
    fresh = generate_token(token_db, name='fresh', scope='read')
    token_db.commit()

    removed = purge_expired(token_db, grace_days=30, now=now)

    assert removed == 2
    remaining = [r.id for r in list_tokens(token_db)]
    assert remaining == [fresh.id]
    _ = expired


# ---------------------------------------------------------------------------
# parse_expires
# ---------------------------------------------------------------------------


def test_parse_expires_none_and_never_and_empty():
    assert parse_expires(None) is None
    assert parse_expires('') is None
    assert parse_expires('never') is None
    assert parse_expires(' NEVER ') is None


def test_parse_expires_duration():
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
    assert parse_expires('90d', now=now) == '2026-07-14T12:00:00Z'
    assert parse_expires('24h', now=now) == '2026-04-16T12:00:00Z'
    assert parse_expires('7D', now=now) == '2026-04-22T12:00:00Z'


def test_parse_expires_iso_date():
    assert parse_expires('2027-01-01') == '2027-01-01T00:00:00Z'


def test_parse_expires_iso_datetime_with_tz():
    # Non-UTC is normalized to UTC.
    assert parse_expires('2027-01-01T05:00:00+05:00') == '2027-01-01T00:00:00Z'


def test_parse_expires_rejects_negative_duration():
    with pytest.raises(ValueError):
        parse_expires('0d')


def test_parse_expires_rejects_junk():
    with pytest.raises(ValueError):
        parse_expires('tomorrow')


# ---------------------------------------------------------------------------
# Decorator: integrates with a throwaway Flask app
# ---------------------------------------------------------------------------


def test_decorator_401_missing_has_www_authenticate(client):
    """A protected route without a Bearer header returns 401 + challenge."""
    from flask import jsonify

    from app import create_app
    from app.services.api_tokens import require_api_token

    # `client` fixture already wired up the app. Register a disposable
    # route on it for the duration of the test.
    app = client.application

    @app.route('/test/api/protected')
    @require_api_token('read')
    def _protected():
        return jsonify({'ok': True})

    resp = app.test_client().get('/test/api/protected')
    assert resp.status_code == 401
    assert resp.headers.get('WWW-Authenticate') == 'Bearer'
    assert resp.get_json() == {'error': 'missing'}
    _ = create_app  # silence linter; kept for clarity


def test_decorator_403_on_scope_mismatch(auth_client, app):
    """Token with only read scope hits a write-scoped route → 403."""
    from flask import jsonify

    from app.db import get_db
    from app.services.api_tokens import generate_token, require_api_token

    @app.route('/test/api/write-only')
    @require_api_token('write')
    def _write_only():
        return jsonify({'ok': True})

    with app.app_context():
        raw = generate_token(get_db(), name='read-only', scope='read').raw

    resp = app.test_client().get(
        '/test/api/write-only',
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert resp.status_code == 403
    assert resp.get_json() == {'error': 'insufficient_scope'}
    assert 'WWW-Authenticate' not in resp.headers
    _ = auth_client  # unused, but ensures fixture parity


def test_decorator_success_sets_g_api_token(app):
    from flask import g, jsonify

    from app.db import get_db
    from app.services.api_tokens import generate_token, require_api_token

    seen = {}

    @app.route('/test/api/who-am-i')
    @require_api_token('read')
    def _who():
        seen['name'] = g.api_token.name
        seen['scope_list'] = list(g.api_token.scope_list)
        return jsonify({'ok': True})

    with app.app_context():
        raw = generate_token(get_db(), name='tester', scope='read,write').raw

    resp = app.test_client().get(
        '/test/api/who-am-i',
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert resp.status_code == 200
    assert seen == {'name': 'tester', 'scope_list': ['read', 'write']}


def test_decorator_rejects_token_in_query_string(app):
    """Only Authorization header is accepted — query strings are ignored."""
    from flask import jsonify

    from app.db import get_db
    from app.services.api_tokens import generate_token, require_api_token

    @app.route('/test/api/no-qs')
    @require_api_token('read')
    def _no_qs():
        return jsonify({'ok': True})

    with app.app_context():
        raw = generate_token(get_db(), name='qs-test', scope='read').raw

    # Passing the token in the query string must NOT authenticate.
    resp = app.test_client().get(f'/test/api/no-qs?token={raw}')
    assert resp.status_code == 401
    assert resp.get_json() == {'error': 'missing'}


# ---------------------------------------------------------------------------
# Rate limit callables
# ---------------------------------------------------------------------------


def test_rate_limit_callables_read_from_settings(app):
    """The limiter callables should reflect the current settings value."""
    from app.db import get_db
    from app.services.api_tokens import (
        rate_limit_admin,
        rate_limit_read,
        rate_limit_write,
    )
    from app.services.settings_svc import invalidate_cache

    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('api_rate_limit_read', '42')"
        )
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('api_rate_limit_write', '7')"
        )
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('api_rate_limit_admin', '3')"
        )
        db.commit()
        invalidate_cache()

        assert rate_limit_read() == '42 per minute'
        assert rate_limit_write() == '7 per minute'
        assert rate_limit_admin() == '3 per minute'


def test_rate_limit_callable_passes_through_full_expression(app):
    """If the setting already includes 'per' it's used verbatim."""
    from app.db import get_db
    from app.services.api_tokens import rate_limit_read
    from app.services.settings_svc import invalidate_cache

    with app.app_context():
        db = get_db()
        db.execute(
            'INSERT OR REPLACE INTO settings(key, value) '
            "VALUES ('api_rate_limit_read', '10 per second')"
        )
        db.commit()
        invalidate_cache()

        assert rate_limit_read() == '10 per second'


def test_rate_limit_callable_falls_back_to_default(app):
    """An empty / missing setting falls back to the hard-coded default."""
    from app.db import get_db
    from app.services.api_tokens import rate_limit_read
    from app.services.settings_svc import invalidate_cache

    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM settings WHERE key = 'api_rate_limit_read'")
        db.commit()
        invalidate_cache()

        assert rate_limit_read() == '60 per minute'
