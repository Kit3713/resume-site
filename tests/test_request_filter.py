"""Tests for the WAF-lite request filter (Phase 13.3)."""

from __future__ import annotations

import json


def test_path_traversal_blocked(client):
    response = client.get('/../../etc/passwd')
    assert response.status_code == 400


def test_path_traversal_encoded(client):
    response = client.get('/%2e%2e%2f%2e%2e%2fetc/passwd')
    assert response.status_code == 400


def test_null_byte_in_path(client):
    response = client.get('/page%00.html')
    assert response.status_code == 400


def test_sql_injection_in_query(client):
    response = client.get("/portfolio?category=' OR 1=1 --")
    assert response.status_code == 400


def test_union_select_in_query(client):
    response = client.get('/blog?tag=x UNION SELECT * FROM users')
    assert response.status_code == 400


def test_normal_request_passes(client):
    response = client.get('/')
    assert response.status_code == 200


def test_normal_query_passes(client):
    response = client.get('/portfolio?category=web')
    assert response.status_code == 200


def test_filter_disabled(client, app):
    db = _get_db(app)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('request_filter_enabled', 'false')"
    )
    db.commit()
    response = client.get('/../../etc/passwd')
    assert response.status_code == 404


def test_log_only_mode(client, app):
    db = _get_db(app)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('request_filter_log_only', 'true')"
    )
    db.commit()
    response = client.get('/../../etc/passwd')
    assert response.status_code == 404


def test_sqli_in_post_body_blocked(client, caplog):
    """#84: POST body with a SQLi fingerprint must be blocked.

    The legacy filter only looked at ``request.query_string``, so an
    attacker probing a SQLi-prone POST endpoint received no WAF-level
    signal. Body inspection (capped at 64 KB) closes the gap.
    """
    payload = json.dumps({'q': "1' OR 1=1 --"}).encode('utf-8')
    with caplog.at_level('WARNING', logger='app.security'):
        response = client.post(
            '/api/v1/anything',
            data=payload,
            content_type='application/json',
        )
    assert response.status_code == 400
    assert any('sql_injection_probe' in r.getMessage() for r in caplog.records)


def test_chunked_transfer_above_max_blocked(app):
    """#85: a chunked request whose body exceeds MAX_CONTENT_LENGTH
    must not slip past the size gate.

    Werkzeug's test client doesn't natively simulate a real chunked
    request — it sets both ``Transfer-Encoding: chunked`` *and* a
    Content-Length, which the WSGI layer treats as size-known. To
    force the size-unknown chunked path, we set
    ``wsgi.input_terminated`` directly (the marker a real chunk-aware
    server like gunicorn sets), drop Content-Length, and POST through
    a synthetic environ.

    With a body larger than ``MAX_CONTENT_LENGTH``, Werkzeug's
    ``LimitedStream`` raises ``RequestEntityTooLarge`` (413) on the
    over-read attempt. The filter may also pre-empt with 400 if the
    chunked body missed Content-Type. Either is an acceptable
    rejection — the contract is "oversized chunked body is not
    processed".
    """
    import io

    limit = app.config['MAX_CONTENT_LENGTH']
    oversized = b'A' * (limit + 1024)  # well past the cap
    client = app.test_client()
    environ_overrides = {
        'wsgi.input': io.BytesIO(oversized),
        'wsgi.input_terminated': True,
        'CONTENT_LENGTH': '',  # signal size-unknown like real chunked
    }
    response = client.post(
        '/contact',
        data=oversized,
        headers={
            'Transfer-Encoding': 'chunked',
            'Content-Type': 'application/octet-stream',
        },
        environ_overrides=environ_overrides,
    )
    assert response.status_code in (400, 413)


def test_chunked_transfer_missing_content_type_blocked(client):
    """#85: a chunked request with no Content-Type bypassed the
    missing-CT gate (which only fired when content_length > 0).
    """
    response = client.post(
        '/api/v1/anything',
        data=b'small body',
        headers={'Transfer-Encoding': 'chunked'},
    )
    assert response.status_code == 400


def test_url_encoded_path_traversal_blocked(client):
    """#88: URL- and double-URL-encoded path traversal must be blocked.

    Single-encoded ``%2e%2e%2f`` decodes once to ``../``;
    double-encoded ``%252e%252e%252f`` decodes once to
    ``%2e%2e%2f`` and again to ``../``. The iterative unquote loop
    handles both.
    """
    single = client.get('/api/v1/%2e%2e%2fadmin/secrets')
    assert single.status_code == 400, (
        f'single-encoded traversal should be blocked, got {single.status_code}'
    )

    double = client.get('/api/v1/%252e%252e%252fadmin/secrets')
    assert double.status_code == 400, (
        f'double-encoded traversal should be blocked, got {double.status_code}'
    )


def test_unicode_lookalike_path_traversal_blocked(client):
    """#136: Unicode full-width lookalikes for ``..`` and ``/`` must
    be blocked.

    Full-width period (U+FF0E) and full-width solidus (U+FF0F) are
    visually identical to their ASCII counterparts; some servers fold
    them via NFKC and others don't. Filter normalises post-decode so
    the regex catches both forms.
    """
    # Full-width: ．．／  (U+FF0E U+FF0E U+FF0F)
    fullwidth = '/api/v1/．．／admin/secrets'
    response = client.get(fullwidth)
    assert response.status_code == 400, (
        f'full-width Unicode traversal should be blocked, got {response.status_code}'
    )


def _get_db(app):
    import sqlite3

    db = sqlite3.connect(app.config['DATABASE_PATH'])
    db.row_factory = sqlite3.Row
    return db
