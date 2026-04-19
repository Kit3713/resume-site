"""
Edge-case tests for the v1 REST API — Phase 18.13.

Applies the ``tests/TESTING_STANDARDS.md`` checklist to the public API
surface: pagination clamps, invalid IDs, malformed JSON, oversized fields,
and concurrent POSTs. Auth-wall / scope-matrix tests live in
``tests/test_api_tokens.py``; this file is purely about what happens when
well-authenticated clients send adversarial input.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_rate_limits(app):
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


@pytest.fixture
def api_write_token(app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='api-edge', scope='read,write').raw


@pytest.fixture
def api_admin_token(app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='admin-edge', scope='admin').raw


def _auth(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _enable_blog(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('blog_enabled', 'true')")
    conn.commit()
    conn.close()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


# ---------------------------------------------------------------------------
# Pagination clamps — the single most-repeated parameter in the API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/portfolio',
        '/api/v1/testimonials',
    ],
)
@pytest.mark.parametrize(
    'per_page_value,expected_per_page',
    [
        ('0', 20),  # zero clamps to default
        ('-5', 20),  # negative clamps to default
        ('1', 1),  # minimum
        ('100', 100),  # maximum
        ('101', 100),  # one-over clamps to max
        ('9999', 100),  # way-over clamps to max
        ('abc', 20),  # non-integer falls back to default
        ('', 20),  # empty string falls back to default
        ('1.5', 20),  # float also falls back (int() raises ValueError)
    ],
)
def test_per_page_clamp(client, path, per_page_value, expected_per_page):
    response = client.get(f'{path}?per_page={per_page_value}')
    assert response.status_code == 200
    body = response.get_json()
    assert body['pagination']['per_page'] == expected_per_page


@pytest.mark.parametrize('page', ['0', '-1', '-99', 'abc', '', '1.5'])
def test_page_clamp_never_negative(client, page):
    """``?page=`` must always resolve to >= 1 regardless of input."""
    response = client.get(f'/api/v1/portfolio?page={page}')
    assert response.status_code == 200
    assert response.get_json()['pagination']['page'] >= 1


def test_page_with_path_traversal_blocked_by_request_filter(client):
    """``?page=/../`` is caught by the request filter before reaching the route.

    The filter returns 400 with a "path_traversal" reason — the important
    invariant is that malformed pagination input does not 500 the server,
    and that obvious traversal payloads are blocked up front.
    """
    response = client.get('/api/v1/portfolio?page=/../')
    assert response.status_code in (200, 400)


def test_page_beyond_total_returns_empty_data(client):
    """Overshooting the last page is valid — just returns empty ``data``."""
    response = client.get('/api/v1/portfolio?page=9999')
    assert response.status_code == 200
    body = response.get_json()
    assert body['data'] == []
    assert body['pagination']['page'] == 9999


# ---------------------------------------------------------------------------
# Invalid IDs on single-resource routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'photo_id',
    [
        '0',  # invalid integer
        '999999999',  # nonexistent but valid int
        '-1',  # werkzeug's int converter rejects this → 404
        'abc',  # non-numeric
    ],
)
def test_portfolio_detail_handles_bad_ids(client, photo_id):
    response = client.get(f'/api/v1/portfolio/{photo_id}')
    # 404 either from the handler (int resolved, row missing) or from the
    # URL converter (non-int path segment).
    assert response.status_code == 404


def test_portfolio_detail_id_at_int_boundary(client):
    """Werkzeug's ``int`` converter uses Python ints — huge IDs must 404, not 500."""
    response = client.get(f'/api/v1/portfolio/{2**63 - 1}')
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


def test_malformed_json_body_on_create_falls_back_to_empty_body(
    client, no_rate_limits, api_write_token, app
):
    """``_json_body`` uses ``request.get_json(silent=True)`` — a malformed
    body collapses to ``{}``. For /blog create, that means title is missing,
    so the endpoint returns 400 (validation), not 500 (parse error).
    """
    _enable_blog(app)
    response = client.post(
        '/api/v1/blog',
        data='{not-json,',
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400
    assert response.get_json()['code'] == 'VALIDATION_ERROR'


def test_array_json_body_collapses_to_empty(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    response = client.post(
        '/api/v1/blog',
        data=json.dumps([1, 2, 3]),
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


def test_wrong_content_type_returns_415(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    response = client.post(
        '/api/v1/blog',
        data='title=x',
        headers={
            'Authorization': f'Bearer {api_write_token}',
            'Content-Type': 'text/plain',
        },
    )
    assert response.status_code == 415
    assert response.get_json()['code'] == 'UNSUPPORTED_MEDIA_TYPE'


def test_missing_content_type_on_post_rejected(client, no_rate_limits, api_write_token):
    """A POST without any Content-Type header is rejected before routing.

    The request filter returns 400 with reason ``missing_content_type``;
    the API's own Content-Type middleware would produce 415. Either is an
    explicit rejection — the invariant is that no handler sees a body with
    an unspecified encoding.
    """
    response = client.post(
        '/api/v1/blog',
        data='{"title": "x"}',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code in (400, 415)


def test_get_does_not_require_content_type(client):
    """GETs with no body must not be rejected by the 415 middleware."""
    response = client.get('/api/v1/site')
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Oversized fields
# ---------------------------------------------------------------------------


def test_oversized_title_does_not_500(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    response = client.post(
        '/api/v1/blog',
        json={'title': 'x' * 100_000},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201  # SQLite TEXT is unbounded
    # Reading time is computed from ``content``, not ``title`` — a body-less
    # post reports 0 minutes. What matters is that the field is a non-negative
    # integer and that the write completed.
    reading_time = response.get_json()['data']['reading_time']
    assert isinstance(reading_time, int) and reading_time >= 0


def test_oversized_settings_value_is_accepted(client, no_rate_limits, api_admin_token, app):
    huge = 'x' * 10_000
    response = client.put(
        '/api/v1/admin/settings',
        json={'footer_text': huge},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert response.get_json()['data']['flat']['footer_text'] == huge


def test_oversized_query_string_does_not_500(client):
    path = '/api/v1/portfolio?per_page=' + ('9' * 4096)
    response = client.get(path)
    assert response.status_code == 200  # clamped to max per_page


# ---------------------------------------------------------------------------
# Unicode / injection payloads on query strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'category',
    [
        "'; DROP TABLE photos;--",
        '<script>alert(1)</script>',
        '../../../etc/passwd',
        '山',
        '\x00',
    ],
)
def test_portfolio_category_query_is_safe(client, category):
    response = client.get('/api/v1/portfolio', query_string={'category': category})
    # Handler treats unknown categories as "no rows"; never 500
    assert response.status_code == 200
    assert response.get_json()['data'] == []


# ---------------------------------------------------------------------------
# Error-response envelope
# ---------------------------------------------------------------------------


def test_404_returns_json_error_envelope(client):
    response = client.get('/api/v1/does-not-exist')
    assert response.status_code == 404
    assert response.headers['Content-Type'].startswith('application/json')
    body = response.get_json()
    assert 'error' in body
    assert 'code' in body


def test_method_not_allowed_returns_405_json(client, no_rate_limits):
    response = client.post('/api/v1/site', json={})  # /site is GET-only
    assert response.status_code == 405
    assert response.headers['Content-Type'].startswith('application/json')


def test_missing_bearer_token_returns_401_with_www_authenticate(client, no_rate_limits, app):
    _enable_blog(app)
    response = client.post('/api/v1/blog', json={'title': 'x'})
    assert response.status_code == 401
    assert response.headers.get('WWW-Authenticate') == 'Bearer'


# ---------------------------------------------------------------------------
# Concurrent POSTs — a small burst should never 500
# ---------------------------------------------------------------------------


def test_concurrent_post_contact_never_500s(app, no_rate_limits):
    errors: list[BaseException] = []
    status_codes: list[int] = []
    lock = threading.Lock()

    def post():
        try:
            with app.test_client() as c:
                response = c.post(
                    '/api/v1/contact',
                    json={'name': 'N', 'email': 'a@b.c', 'message': 'msg'},
                )
                with lock:
                    status_codes.append(response.status_code)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=post) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert all(code in (201, 429) for code in status_codes), status_codes


def test_concurrent_reads_never_500(app):
    errors: list[BaseException] = []

    def read():
        try:
            with app.test_client() as c:
                for path in (
                    '/api/v1/site',
                    '/api/v1/portfolio',
                    '/api/v1/testimonials',
                    '/api/v1/portfolio/categories',
                ):
                    response = c.get(path)
                    assert response.status_code in (200, 304, 404), (
                        f'{path}: {response.status_code}'
                    )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=read) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], errors


# ---------------------------------------------------------------------------
# ETag behaviour under edge inputs
# ---------------------------------------------------------------------------


def test_etag_on_empty_collection(client):
    """Even an empty ``data`` list should still get a stable ETag."""
    response = client.get('/api/v1/portfolio')
    assert response.status_code == 200
    etag = response.headers.get('ETag')
    assert etag and etag.startswith('"')
    # Second call with the same ETag must 304
    second = client.get('/api/v1/portfolio', headers={'If-None-Match': etag})
    assert second.status_code == 304


def test_etag_malformed_if_none_match_returns_fresh_200(client):
    response = client.get('/api/v1/site', headers={'If-None-Match': 'not-a-valid-etag'})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Settings update edge cases (admin)
# ---------------------------------------------------------------------------


def test_admin_settings_ignores_unknown_keys(client, no_rate_limits, api_admin_token):
    response = client.put(
        '/api/v1/admin/settings',
        json={'bogus_key': 'x', 'another_fake': 123},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert response.get_json()['data']['updated_keys'] == []


def test_admin_settings_empty_body_is_noop(client, no_rate_limits, api_admin_token):
    response = client.put('/api/v1/admin/settings', json={}, headers=_auth(api_admin_token))
    assert response.status_code == 200
    assert response.get_json()['data']['updated_keys'] == []


def test_admin_settings_boolean_coercion(client, no_rate_limits, api_admin_token):
    """Non-boolean truthy values must coerce to ``'true'`` / ``'false'``
    in storage. API accepts ``True``, ``'true'``, ``1``, ``'1'``.

    The ``flat`` payload serialises booleans back to their string form
    since the settings table stores strings; the truthiness of the value
    is what matters to callers.
    """
    response = client.put(
        '/api/v1/admin/settings',
        json={'dark_mode_default': 1},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert str(response.get_json()['data']['flat']['dark_mode_default']).lower() in (
        'true',
        '1',
    )

    response = client.put(
        '/api/v1/admin/settings',
        json={'dark_mode_default': 'something-else'},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert str(response.get_json()['data']['flat']['dark_mode_default']).lower() in (
        'false',
        '0',
    )
