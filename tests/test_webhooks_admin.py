"""
Webhooks Admin UI + REST API Tests — Phase 19.2 (admin surface)

Covers the operator-facing layer on top of ``app/services/webhooks.py``:

* HTML routes under ``/admin/webhooks`` — auth gating, IP restriction,
  CRUD, the synchronous ``/test`` button, the per-webhook delivery log.
* JSON routes under ``/api/v1/admin/webhooks`` — list, create (with
  one-time secret echo), get, update (including ``reset_failures``),
  delete, the ``/test`` shortcut, and the deliveries log.
* Cross-cutting contracts: secrets are NEVER returned outside the
  create-response payload; every read endpoint honours ETag /
  If-None-Match; every write endpoint mutates ``admin_activity_log``.

The dispatcher tests (auto-disable, async fan-out, bus integration)
already live in ``tests/test_webhooks.py`` — this file is strictly
about the admin / API adapter layer, so it patches ``urlopen`` only
where the synchronous test-delivery endpoint actually fires.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import app.events as events_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_bus():
    """Webhook tests must start and end with an empty event registry."""
    events_mod.clear()
    yield
    events_mod.clear()


@pytest.fixture
def db(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    yield conn
    conn.close()


@pytest.fixture
def no_rate_limits(app):
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


@pytest.fixture
def api_admin_token(app):
    """Admin-scoped Bearer token for the JSON routes."""
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='webhooks-bot', scope='admin').raw


def _admin_headers(token, *, with_json=False):
    headers = {'Authorization': f'Bearer {token}'}
    if with_json:
        headers['Content-Type'] = 'application/json'
    return headers


def _seed_webhook(db, **overrides):
    """Insert a webhook row directly and return its id."""
    from app.services.webhooks import create_webhook

    fields = {
        'name': 'fixture',
        'url': 'https://example.test/h',
        'secret': 'shh',
        'events': ['*'],
        'enabled': True,
    }
    fields.update(overrides)
    return create_webhook(db, **fields)


def _activity_count(app, *, category='webhooks'):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        return conn.execute(
            'SELECT COUNT(*) FROM admin_activity_log WHERE category = ?',
            (category,),
        ).fetchone()[0]
    finally:
        conn.close()


def _webhook_row(app, webhook_id):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute('SELECT * FROM webhooks WHERE id = ?', (webhook_id,)).fetchone()
    finally:
        conn.close()


class _StubResponse:
    """Drop-in for the urlopen() context manager."""

    def __init__(self, status=200):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ===========================================================================
# Admin HTML routes
# ===========================================================================


@pytest.mark.parametrize(
    'path,method',
    [
        ('/admin/webhooks', 'GET'),
        ('/admin/webhooks/create', 'POST'),
        ('/admin/webhooks/1/update', 'POST'),
        ('/admin/webhooks/1/delete', 'POST'),
        ('/admin/webhooks/1/test', 'POST'),
        ('/admin/webhooks/1/deliveries', 'GET'),
    ],
)
def test_admin_routes_require_auth(client, path, method):
    """Unauthenticated requests redirect to /admin/login."""
    fn = client.get if method == 'GET' else client.post
    response = fn(path, follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_admin_webhooks_ip_restriction(app):
    """IP gate inherited from the admin blueprint applies here too."""
    client = app.test_client()
    response = client.get(
        '/admin/webhooks', headers={'X-Forwarded-For': '203.0.113.42'}
    )
    assert response.status_code == 403


def test_admin_webhooks_empty_page_renders(auth_client):
    response = auth_client.get('/admin/webhooks')
    assert response.status_code == 200
    body = response.data.decode('utf-8')
    assert 'Add Webhook' in body
    assert 'No webhooks configured' in body
    # The pre-filled secret must look like a 32-byte URL-safe value
    # (>= 40 chars). Spot-check by asserting the input is present.
    assert 'name="secret"' in body


def test_admin_webhooks_lists_existing_rows(auth_client, db):
    _seed_webhook(db, name='Slack', url='https://hooks.example/abc')
    response = auth_client.get('/admin/webhooks')
    assert response.status_code == 200
    body = response.data.decode('utf-8')
    assert 'Slack' in body
    assert 'https://hooks.example/abc' in body
    # The recent-deliveries panel is hidden when there are zero rows.
    assert 'Recent Deliveries' not in body


def test_admin_webhooks_create_persists_row_and_logs(auth_client, app):
    response = auth_client.post(
        '/admin/webhooks/create',
        data={
            'name': 'My Hook',
            'url': 'https://example.test/x',
            'secret': 'manual-secret',
            'events': 'blog.published, contact.submitted',
            'enabled': '1',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin/webhooks')

    rows = _webhook_row(app, 1)
    assert rows is not None
    assert rows['name'] == 'My Hook'
    assert rows['url'] == 'https://example.test/x'
    assert rows['secret'] == 'manual-secret'
    assert rows['enabled'] == 1
    # JSON payload is preserved verbatim.
    assert json.loads(rows['events']) == ['blog.published', 'contact.submitted']
    assert _activity_count(app) == 1


def test_admin_webhooks_create_rejects_missing_name(auth_client, app):
    response = auth_client.post(
        '/admin/webhooks/create',
        data={'url': 'https://example.test/x'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Name is required' in response.data
    assert _webhook_row(app, 1) is None


def test_admin_webhooks_create_rejects_bad_url(auth_client, app):
    response = auth_client.post(
        '/admin/webhooks/create',
        data={'name': 'X', 'url': 'not-a-url'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'URL must be a valid http(s) address' in response.data
    assert _webhook_row(app, 1) is None


def test_admin_webhooks_create_defaults_events_to_wildcard(auth_client, app):
    auth_client.post(
        '/admin/webhooks/create',
        data={'name': 'X', 'url': 'https://e/x', 'events': ''},
    )
    row = _webhook_row(app, 1)
    assert json.loads(row['events']) == ['*']


def test_admin_webhooks_update_writes_partial_fields(auth_client, app, db):
    wh_id = _seed_webhook(db, name='Old', url='https://e/old', secret='orig')
    response = auth_client.post(
        f'/admin/webhooks/{wh_id}/update',
        data={'name': 'New', 'url': 'https://e/new', 'events': 'a, b'},
    )
    assert response.status_code == 302
    row = _webhook_row(app, wh_id)
    assert row['name'] == 'New'
    assert row['url'] == 'https://e/new'
    # Empty secret in the form means "keep".
    assert row['secret'] == 'orig'
    assert json.loads(row['events']) == ['a', 'b']


def test_admin_webhooks_update_rotates_secret_when_provided(auth_client, db, app):
    wh_id = _seed_webhook(db, secret='old')
    auth_client.post(
        f'/admin/webhooks/{wh_id}/update',
        data={'secret': 'rotated'},
    )
    assert _webhook_row(app, wh_id)['secret'] == 'rotated'


def test_admin_webhooks_update_resets_failures_when_checkbox_set(auth_client, db, app):
    from app.services.webhooks import increment_failures

    wh_id = _seed_webhook(db)
    increment_failures(db, wh_id, threshold=10)
    increment_failures(db, wh_id, threshold=10)
    assert _webhook_row(app, wh_id)['failure_count'] == 2

    auth_client.post(
        f'/admin/webhooks/{wh_id}/update',
        data={'reset_failures': '1'},
    )
    assert _webhook_row(app, wh_id)['failure_count'] == 0


def test_admin_webhooks_update_404_for_missing_id(auth_client, app):
    response = auth_client.post(
        '/admin/webhooks/9999/update',
        data={'name': 'X'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Webhook not found' in response.data


def test_admin_webhooks_delete_removes_row(auth_client, db, app):
    wh_id = _seed_webhook(db)
    auth_client.post(f'/admin/webhooks/{wh_id}/delete')
    assert _webhook_row(app, wh_id) is None


def test_admin_webhooks_test_button_records_success(auth_client, db, app, monkeypatch):
    wh_id = _seed_webhook(db)
    monkeypatch.setattr(
        'app.services.webhooks.urlopen',
        lambda req, timeout=None: _StubResponse(204),
    )
    response = auth_client.post(
        f'/admin/webhooks/{wh_id}/test', follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Test delivery succeeded' in response.data
    # One delivery row written + counter still 0.
    fresh = sqlite3.connect(app.config['DATABASE_PATH'])
    fresh.row_factory = sqlite3.Row
    try:
        rows = fresh.execute(
            'SELECT * FROM webhook_deliveries WHERE webhook_id = ?', (wh_id,)
        ).fetchall()
        wh = fresh.execute(
            'SELECT failure_count FROM webhooks WHERE id = ?', (wh_id,)
        ).fetchone()
    finally:
        fresh.close()
    assert len(rows) == 1
    assert rows[0]['status_code'] == 204
    assert wh['failure_count'] == 0


def test_admin_webhooks_test_button_records_failure(auth_client, db, app, monkeypatch):
    from urllib.error import URLError

    wh_id = _seed_webhook(db)
    monkeypatch.setattr(
        'app.services.webhooks.urlopen',
        lambda req, timeout=None: (_ for _ in ()).throw(URLError('boom')),
    )
    response = auth_client.post(
        f'/admin/webhooks/{wh_id}/test', follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Test delivery failed' in response.data
    assert _webhook_row(app, wh_id)['failure_count'] == 1


def test_admin_webhooks_deliveries_page_shows_log(auth_client, db, app):
    from app.services.webhooks import DeliveryResult, record_delivery

    wh_id = _seed_webhook(db, name='Loggy')
    record_delivery(db, DeliveryResult(wh_id, 'blog.published', 200, 12, ''))
    response = auth_client.get(f'/admin/webhooks/{wh_id}/deliveries')
    assert response.status_code == 200
    body = response.data.decode('utf-8')
    assert 'Loggy' in body
    assert 'blog.published' in body
    assert '200' in body


def test_admin_webhooks_deliveries_404_for_missing_id(auth_client):
    response = auth_client.get(
        '/admin/webhooks/9999/deliveries', follow_redirects=True
    )
    assert response.status_code == 200
    assert b'Webhook not found' in response.data


# ===========================================================================
# REST API routes
# ===========================================================================


def test_api_admin_webhooks_requires_auth(client, no_rate_limits):
    assert client.get('/api/v1/admin/webhooks').status_code == 401
    # JSON content-type middleware runs before auth, so a body is needed
    # to avoid a 415 false negative.
    assert (
        client.post(
            '/api/v1/admin/webhooks',
            json={'name': 'X', 'url': 'https://e/x'},
        ).status_code
        == 401
    )


def test_api_admin_webhooks_rejects_write_scope(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        write_token = generate_token(get_db(), name='w', scope='write').raw

    response = client.get(
        '/api/v1/admin/webhooks',
        headers={'Authorization': f'Bearer {write_token}'},
    )
    assert response.status_code == 403


def test_api_admin_webhooks_list_returns_no_secrets(client, no_rate_limits, api_admin_token, db):
    _seed_webhook(db, name='Slack', secret='very-secret')
    response = client.get(
        '/api/v1/admin/webhooks', headers=_admin_headers(api_admin_token)
    )
    assert response.status_code == 200
    body = response.get_json()
    assert len(body['data']) == 1
    entry = body['data'][0]
    assert entry['name'] == 'Slack'
    assert 'secret' not in entry
    # Ensure the secret string itself never appears in the bytes either.
    assert b'very-secret' not in response.data


def test_api_admin_webhooks_create_round_trip(client, no_rate_limits, api_admin_token, app):
    response = client.post(
        '/api/v1/admin/webhooks',
        json={
            'name': 'Slack',
            'url': 'https://hooks.example/abc',
            'events': ['blog.published'],
        },
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 201
    body = response.get_json()
    payload = body['data']
    assert payload['name'] == 'Slack'
    assert payload['url'] == 'https://hooks.example/abc'
    assert payload['events'] == ['blog.published']
    assert payload['enabled'] is True
    # Secret is echoed exactly once on creation.
    assert payload['secret']
    assert len(payload['secret']) >= 30

    # Subsequent GET must NOT include the secret.
    detail = client.get(
        f'/api/v1/admin/webhooks/{payload["id"]}',
        headers=_admin_headers(api_admin_token),
    )
    assert detail.status_code == 200
    assert 'secret' not in detail.get_json()['data']


def test_api_admin_webhooks_create_accepts_csv_events(
    client, no_rate_limits, api_admin_token, app
):
    response = client.post(
        '/api/v1/admin/webhooks',
        json={
            'name': 'X',
            'url': 'https://e/x',
            'events': 'a, b , c',
        },
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 201
    assert response.get_json()['data']['events'] == ['a', 'b', 'c']


def test_api_admin_webhooks_create_uses_supplied_secret(
    client, no_rate_limits, api_admin_token, app
):
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'X', 'url': 'https://e/x', 'secret': 'pinned'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 201
    assert response.get_json()['data']['secret'] == 'pinned'


def test_api_admin_webhooks_create_rejects_missing_url(
    client, no_rate_limits, api_admin_token
):
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'X'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['details']['field'] == 'url'


def test_api_admin_webhooks_create_rejects_bad_scheme(
    client, no_rate_limits, api_admin_token
):
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'X', 'url': 'ftp://example/'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 400
    assert response.get_json()['details']['field'] == 'url'


def test_api_admin_webhooks_get_404_for_missing(
    client, no_rate_limits, api_admin_token
):
    response = client.get(
        '/api/v1/admin/webhooks/9999', headers=_admin_headers(api_admin_token)
    )
    assert response.status_code == 404
    assert response.get_json()['code'] == 'NOT_FOUND'


def test_api_admin_webhooks_update_partial(
    client, no_rate_limits, api_admin_token, db, app
):
    wh_id = _seed_webhook(db, name='Old', secret='orig')
    response = client.put(
        f'/api/v1/admin/webhooks/{wh_id}',
        json={'name': 'New'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body['data']['name'] == 'New'
    assert 'secret' not in body['data']
    # Secret untouched when not in body.
    assert _webhook_row(app, wh_id)['secret'] == 'orig'


def test_api_admin_webhooks_update_rotates_secret(
    client, no_rate_limits, api_admin_token, db, app
):
    wh_id = _seed_webhook(db, secret='orig')
    client.put(
        f'/api/v1/admin/webhooks/{wh_id}',
        json={'secret': 'rotated'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert _webhook_row(app, wh_id)['secret'] == 'rotated'


def test_api_admin_webhooks_update_reset_failures(
    client, no_rate_limits, api_admin_token, db, app
):
    from app.services.webhooks import increment_failures

    wh_id = _seed_webhook(db)
    increment_failures(db, wh_id, threshold=10)
    increment_failures(db, wh_id, threshold=10)
    assert _webhook_row(app, wh_id)['failure_count'] == 2

    response = client.put(
        f'/api/v1/admin/webhooks/{wh_id}',
        json={'reset_failures': True},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    assert _webhook_row(app, wh_id)['failure_count'] == 0


def test_api_admin_webhooks_update_rejects_empty_secret(
    client, no_rate_limits, api_admin_token, db
):
    wh_id = _seed_webhook(db)
    response = client.put(
        f'/api/v1/admin/webhooks/{wh_id}',
        json={'secret': ''},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 400
    assert response.get_json()['details']['field'] == 'secret'


def test_api_admin_webhooks_delete_returns_204(
    client, no_rate_limits, api_admin_token, db, app
):
    wh_id = _seed_webhook(db)
    response = client.delete(
        f'/api/v1/admin/webhooks/{wh_id}', headers=_admin_headers(api_admin_token)
    )
    assert response.status_code == 204
    assert _webhook_row(app, wh_id) is None


def test_api_admin_webhooks_test_success_path(
    client, no_rate_limits, api_admin_token, db, app, monkeypatch
):
    wh_id = _seed_webhook(db)
    monkeypatch.setattr(
        'app.services.webhooks.urlopen',
        lambda req, timeout=None: _StubResponse(200),
    )
    response = client.post(
        f'/api/v1/admin/webhooks/{wh_id}/test',
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body['data']['ok'] is True
    assert body['data']['status_code'] == 200
    assert body['data']['error'] == ''


def test_api_admin_webhooks_test_failure_path_increments_counter(
    client, no_rate_limits, api_admin_token, db, app, monkeypatch
):
    from urllib.error import URLError

    wh_id = _seed_webhook(db)
    monkeypatch.setattr(
        'app.services.webhooks.urlopen',
        lambda req, timeout=None: (_ for _ in ()).throw(URLError('down')),
    )
    response = client.post(
        f'/api/v1/admin/webhooks/{wh_id}/test',
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body['data']['ok'] is False
    assert body['data']['status_code'] == 0
    assert 'URLError' in body['data']['error']
    assert _webhook_row(app, wh_id)['failure_count'] == 1


def test_api_admin_webhooks_deliveries_returns_log(
    client, no_rate_limits, api_admin_token, db
):
    from app.services.webhooks import DeliveryResult, record_delivery

    wh_id = _seed_webhook(db)
    record_delivery(db, DeliveryResult(wh_id, 'blog.published', 200, 12, ''))
    record_delivery(db, DeliveryResult(wh_id, 'contact.submitted', 500, 7, 'oops'))

    response = client.get(
        f'/api/v1/admin/webhooks/{wh_id}/deliveries',
        headers=_admin_headers(api_admin_token),
    )
    assert response.status_code == 200
    rows = response.get_json()['data']
    assert len(rows) == 2
    # Newest first.
    assert rows[0]['event'] == 'contact.submitted'
    assert rows[1]['event'] == 'blog.published'


def test_api_admin_webhooks_deliveries_404_for_missing_id(
    client, no_rate_limits, api_admin_token
):
    response = client.get(
        '/api/v1/admin/webhooks/9999/deliveries',
        headers=_admin_headers(api_admin_token),
    )
    assert response.status_code == 404


def test_api_admin_webhooks_list_etag_round_trip(
    client, no_rate_limits, api_admin_token, db
):
    """Read endpoints share the canonical ETag/If-None-Match contract."""
    _seed_webhook(db, name='ETag-able')
    first = client.get(
        '/api/v1/admin/webhooks', headers=_admin_headers(api_admin_token)
    )
    assert first.status_code == 200
    etag = first.headers.get('ETag')
    assert etag

    second = client.get(
        '/api/v1/admin/webhooks',
        headers={**_admin_headers(api_admin_token), 'If-None-Match': etag},
    )
    assert second.status_code == 304
    assert second.headers.get('ETag') == etag


def test_api_admin_webhooks_create_logs_admin_activity(
    client, no_rate_limits, api_admin_token, app
):
    before = _activity_count(app)
    client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'X', 'url': 'https://e/x'},
        headers=_admin_headers(api_admin_token, with_json=True),
    )
    assert _activity_count(app) == before + 1
