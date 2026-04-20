"""
Admin UI — API Token Management Tests (Phase 13.4)

Exercises the admin routes for API token lifecycle:
``GET /admin/api-tokens``, ``POST /admin/api-tokens/generate``,
``GET /admin/api-tokens/reveal``, ``POST /admin/api-tokens/<id>/revoke``.

The reveal page is the security-sensitive surface: the raw token is
displayed exactly once, then the session key is popped so refresh /
back-button cannot recover the value.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.events import Events, clear, register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_rows(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT id, name, scope, expires_at, revoked FROM api_tokens ORDER BY id'
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Auth / access control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'path',
    [
        '/admin/api-tokens',
        '/admin/api-tokens/reveal',
    ],
)
def test_api_tokens_requires_auth(client, path):
    """Unauthenticated GETs should redirect to login."""
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_api_tokens_ip_restriction(app):
    """IP restriction from the admin blueprint also applies here."""
    client = app.test_client()
    response = client.get(
        '/admin/api-tokens',
        headers={'X-Forwarded-For': '203.0.113.42'},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# List page
# ---------------------------------------------------------------------------


def test_api_tokens_empty_page_loads(auth_client):
    response = auth_client.get('/admin/api-tokens')
    assert response.status_code == 200
    assert b'Generate New Token' in response.data
    assert b'No API tokens generated yet' in response.data


def test_api_tokens_list_shows_generated_token(auth_client, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        generate_token(get_db(), name='CI', scope='read,write')

    response = auth_client.get('/admin/api-tokens')
    assert response.status_code == 200
    assert b'CI' in response.data
    assert b'read' in response.data
    assert b'write' in response.data
    assert b'Active' in response.data


# ---------------------------------------------------------------------------
# Generate → reveal flow
# ---------------------------------------------------------------------------


def test_api_tokens_generate_redirects_to_reveal(auth_client, app):
    response = auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'CI', 'scope': ['read', 'write'], 'expires': 'never'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert '/admin/api-tokens/reveal' in response.headers['Location']

    rows = _token_rows(app)
    assert len(rows) == 1
    assert rows[0]['name'] == 'CI'
    assert rows[0]['scope'] == 'read,write'
    assert rows[0]['expires_at'] is None


def test_api_tokens_reveal_shows_token_once(auth_client):
    """First GET reveals the raw token; second GET has nothing to show."""
    auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'CI', 'scope': ['read'], 'expires': 'never'},
    )

    reveal = auth_client.get('/admin/api-tokens/reveal')
    assert reveal.status_code == 200
    assert b'Save this token now' in reveal.data
    assert b'Bearer Token' in reveal.data
    # The raw token is an attribute on the <code> element via data-token.
    assert b'data-token' in reveal.data

    # Second GET: session key has been popped, so we're redirected home.
    second = auth_client.get('/admin/api-tokens/reveal', follow_redirects=False)
    assert second.status_code == 302
    assert '/admin/api-tokens' in second.headers['Location']


def test_api_tokens_generate_emits_event(auth_client, app):
    captured = []

    def _handler(**payload):
        captured.append(payload)

    clear()
    register(Events.API_TOKEN_CREATED, _handler)
    try:
        auth_client.post(
            '/admin/api-tokens/generate',
            data={'name': 'Integ', 'scope': ['read'], 'expires': '90d'},
        )
    finally:
        clear()

    assert len(captured) == 1
    payload = captured[0]
    assert payload['name'] == 'Integ'
    assert payload['scope'] == 'read'
    assert 'token_id' in payload
    # Defence in depth: the event payload must never carry the raw value.
    assert 'raw' not in payload
    assert 'token_hash' not in payload
    _ = app


def test_api_tokens_generate_requires_name(auth_client, app):
    response = auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': '', 'scope': ['read'], 'expires': 'never'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert _token_rows(app) == []


def test_api_tokens_generate_requires_scope(auth_client, app):
    response = auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'CI', 'scope': [], 'expires': 'never'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert _token_rows(app) == []


def test_api_tokens_generate_rejects_bad_expiry(auth_client, app):
    response = auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'CI', 'scope': ['read'], 'expires': 'tomorrow'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert _token_rows(app) == []


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_api_tokens_revoke_flips_bit(auth_client, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        token = generate_token(get_db(), name='R', scope='read')

    response = auth_client.post(
        f'/admin/api-tokens/{token.id}/revoke',
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert '/admin/api-tokens' in response.headers['Location']

    rows = _token_rows(app)
    assert rows[0]['revoked'] == 1


def test_api_tokens_revoke_missing_id_redirects(auth_client):
    response = auth_client.post(
        '/admin/api-tokens/9999/revoke',
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_api_tokens_revoke_writes_activity_log(auth_client, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        token = generate_token(get_db(), name='LOG', scope='read')

    auth_client.post(f'/admin/api-tokens/{token.id}/revoke')

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT action, category FROM admin_activity_log '
        "WHERE category = 'api_tokens' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert 'Revoked' in row['action']


# ---------------------------------------------------------------------------
# Phase 22.4 — Server-side reveal handoff
# ---------------------------------------------------------------------------


def _reveal_rows(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT reveal_id, token_id, raw_token, expires_at FROM api_token_reveals'
    ).fetchall()
    conn.close()
    return rows


def test_generate_does_not_leak_raw_token_into_session_cookie(auth_client, app):
    """The raw token must never travel in the Set-Cookie payload of
    the generate response. Flask default sessions are signed-not-
    encrypted, so bytes written into ``session['...']`` land in the
    browser's cookie jar in inspectable form."""
    # Before my change: session['_api_token_reveal']['raw'] carried the
    # plaintext — grepping the cookie payload would turn it up.
    post = auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'CookieGate', 'scope': ['read'], 'expires': 'never'},
        follow_redirects=False,
    )
    assert post.status_code == 302
    set_cookie = ''
    for key, value in post.headers.items():
        if key.lower() == 'set-cookie':
            set_cookie += value

    # Grab the raw token from the reveal page so we know the byte string
    # we're searching for.
    reveal = auth_client.get('/admin/api-tokens/reveal')
    assert reveal.status_code == 200
    # The <code> element carries data-token="<raw>".
    import re as _re

    match = _re.search(rb'data-token="([^"]+)"', reveal.data)
    assert match, 'raw token not found in reveal HTML'
    raw = match.group(1).decode('ascii')
    assert len(raw) >= 30  # sanity-check it looks like a real 32-byte urlsafe

    assert raw not in set_cookie, (
        'raw token bytes leaked into Set-Cookie payload — '
        'session carrier should hold only the reveal_id'
    )


def test_reveal_row_is_deleted_after_first_get(auth_client, app):
    """A reveal row must be consumed on first read — not left in the
    DB for a later `SELECT raw_token` attack from a DB-dump leak."""
    auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'OneShot', 'scope': ['read'], 'expires': 'never'},
    )
    assert len(_reveal_rows(app)) == 1

    resp = auth_client.get('/admin/api-tokens/reveal')
    assert resp.status_code == 200
    assert _reveal_rows(app) == []


def test_reveal_expired_returns_410_gone(auth_client, app):
    """A reveal whose 5-minute TTL elapsed must not render the token —
    410 Gone tells the operator the value is permanently unavailable."""
    # Generate normally.
    auth_client.post(
        '/admin/api-tokens/generate',
        data={'name': 'Expiry', 'scope': ['read'], 'expires': 'never'},
    )
    # Backdate the reveal row's expires_at so it's already stale.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute("UPDATE api_token_reveals SET expires_at = '2020-01-01T00:00:00Z'")
    conn.commit()
    conn.close()

    resp = auth_client.get('/admin/api-tokens/reveal')
    assert resp.status_code == 410
    # And the row is gone (consume always deletes).
    assert _reveal_rows(app) == []


def test_reveal_missing_session_id_redirects(auth_client):
    """A GET with no stashed reveal_id (cleared session / never generated)
    must redirect, not 500 or 410."""
    resp = auth_client.get('/admin/api-tokens/reveal', follow_redirects=False)
    assert resp.status_code == 302
    assert '/admin/api-tokens' in resp.headers['Location']


def test_reveal_prune_removes_stale_rows(app):
    """``prune_expired_reveals`` scrubs rows whose TTL elapsed. Called
    request-time by the generate + reveal routes so the table can't
    accumulate forgotten rows indefinitely."""
    from app.services.api_token_reveals import prune_expired_reveals

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.executemany(
        'INSERT INTO api_token_reveals '
        '(reveal_id, token_id, raw_token, name, scope, expires_at) '
        'VALUES (?, 0, ?, ?, ?, ?)',
        [
            ('stale1', 'r1', 'n1', 'read', '2020-01-01T00:00:00Z'),
            ('fresh1', 'r2', 'n2', 'read', '2099-01-01T00:00:00Z'),
            ('stale2', 'r3', 'n3', 'read', '2020-02-02T00:00:00Z'),
        ],
    )
    conn.commit()
    try:
        removed = prune_expired_reveals(conn)
        remaining = [
            r[0] for r in conn.execute('SELECT reveal_id FROM api_token_reveals').fetchall()
        ]
    finally:
        conn.close()
    assert removed == 2
    assert remaining == ['fresh1']
