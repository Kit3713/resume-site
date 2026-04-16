"""Tests for the WAF-lite request filter (Phase 13.3)."""

from __future__ import annotations


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


def _get_db(app):
    import sqlite3

    db = sqlite3.connect(app.config['DATABASE_PATH'])
    db.row_factory = sqlite3.Row
    return db
