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
