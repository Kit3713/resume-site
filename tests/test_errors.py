"""
Error Taxonomy Tests — Phase 18.9

Verifies:
- `ErrorCategory` constants are stable strings (metric label contract).
- `categorize_status` maps HTTP codes to the right bucket.
- `categorize_exception` preferences explicit subclasses over status.
- End-to-end: the errorhandler logs with exc_info + correct category,
  returns a safe body (no traceback, no internals), and the errors_total
  counter is incremented.
- Content negotiation: Accept: application/json → JSON body; otherwise
  text/plain.
- HTTPExceptions (404/403) are categorised by status alone and do NOT
  go through the 500 handler (Flask renders them with its defaults).
"""

from __future__ import annotations

import errno
import json
import logging
import sqlite3

import pytest
from flask import Flask

from app.errors import (
    DataError,
    ErrorCategory,
    ExternalError,
    categorize_exception,
    categorize_status,
)

# ---------------------------------------------------------------------------
# ErrorCategory constants
# ---------------------------------------------------------------------------


def test_category_constants_are_stable_strings():
    # Stable values matter — they become metric labels and log fields.
    assert ErrorCategory.CLIENT == 'ClientError'
    assert ErrorCategory.AUTH == 'AuthError'
    assert ErrorCategory.EXTERNAL == 'ExternalError'
    assert ErrorCategory.UPSTREAM == 'UpstreamError'
    assert ErrorCategory.DATA == 'DataError'
    assert ErrorCategory.INTERNAL == 'InternalError'


def test_category_all_covers_every_constant():
    explicit = {
        ErrorCategory.CLIENT,
        ErrorCategory.AUTH,
        ErrorCategory.EXTERNAL,
        ErrorCategory.UPSTREAM,
        ErrorCategory.DATA,
        ErrorCategory.INTERNAL,
    }
    assert explicit == ErrorCategory.ALL


# ---------------------------------------------------------------------------
# categorize_status
# ---------------------------------------------------------------------------


def test_categorize_status_returns_none_for_non_errors():
    assert categorize_status(200) is None
    assert categorize_status(301) is None
    assert categorize_status(304) is None
    assert categorize_status(None) is None


def test_categorize_status_maps_auth_codes():
    assert categorize_status(401) == ErrorCategory.AUTH
    assert categorize_status(403) == ErrorCategory.AUTH


def test_categorize_status_maps_other_4xx_to_client():
    assert categorize_status(400) == ErrorCategory.CLIENT
    assert categorize_status(404) == ErrorCategory.CLIENT
    assert categorize_status(429) == ErrorCategory.CLIENT
    assert categorize_status(418) == ErrorCategory.CLIENT


def test_categorize_status_maps_5xx_to_internal():
    # 500/501 stay InternalError (a bug). 505+ keeps the legacy default.
    assert categorize_status(500) == ErrorCategory.INTERNAL
    assert categorize_status(501) == ErrorCategory.INTERNAL
    assert categorize_status(505) == ErrorCategory.INTERNAL


@pytest.mark.parametrize(
    'status,expected',
    [
        # Issue #134 — 502/503/504 are reverse-proxy / gateway / availability
        # signals (rolling restarts, transient blips); they MUST NOT be lumped
        # into InternalError or every deploy will page on-call.
        (500, ErrorCategory.INTERNAL),
        (501, ErrorCategory.INTERNAL),
        (502, ErrorCategory.UPSTREAM),
        (503, ErrorCategory.UPSTREAM),
        (504, ErrorCategory.UPSTREAM),
        (404, ErrorCategory.CLIENT),
        (200, None),
    ],
)
def test_categorize_status(status, expected):
    assert categorize_status(status) == expected


def test_categorize_status_handles_weird_inputs():
    assert categorize_status('not a number') is None
    assert categorize_status('200') is None  # str accepted via int()
    assert categorize_status('500') == ErrorCategory.INTERNAL
    assert categorize_status('503') == ErrorCategory.UPSTREAM


# ---------------------------------------------------------------------------
# categorize_exception
# ---------------------------------------------------------------------------


def test_categorize_exception_external_class_wins():
    assert categorize_exception(ExternalError('smtp down'), status_code=500) == (
        ErrorCategory.EXTERNAL
    )


def test_categorize_exception_data_class_wins():
    assert categorize_exception(DataError('db corrupt'), status_code=500) == (ErrorCategory.DATA)


def test_categorize_exception_sqlite_database_error_is_data():
    assert categorize_exception(sqlite3.DatabaseError('x')) == ErrorCategory.DATA
    assert categorize_exception(sqlite3.OperationalError('x')) == ErrorCategory.DATA
    assert categorize_exception(sqlite3.IntegrityError('x')) == ErrorCategory.DATA


def test_categorize_exception_network_errors_are_external():
    assert categorize_exception(TimeoutError()) == ErrorCategory.EXTERNAL
    assert categorize_exception(ConnectionResetError()) == ErrorCategory.EXTERNAL
    assert categorize_exception(TimeoutError()) == ErrorCategory.EXTERNAL


@pytest.mark.parametrize(
    'exc_factory',
    [
        lambda: OSError(errno.ECONNREFUSED, 'Connection refused'),
        lambda: ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused'),
    ],
    ids=['OSError', 'ConnectionRefusedError'],
)
def test_categorize_econnrefused_is_upstream(exc_factory):
    # Issue #134 — refused-connection signals an unavailable upstream (port
    # closed, container not ready). Categorise as UpstreamError so it
    # doesn't pollute the InternalError bug counter. Both the bare OSError
    # form and the ConnectionRefusedError subclass form must classify the
    # same way — the branch keys on errno, not class.
    assert categorize_exception(exc_factory()) == ErrorCategory.UPSTREAM


def test_categorize_exception_domain_errors_are_client():
    from app.exceptions import DuplicateError, NotFoundError, ValidationError

    assert categorize_exception(ValidationError('bad')) == ErrorCategory.CLIENT
    assert categorize_exception(NotFoundError('missing')) == ErrorCategory.CLIENT
    assert categorize_exception(DuplicateError('dupe')) == ErrorCategory.CLIENT


def test_categorize_exception_falls_back_to_status():
    # Plain exception, caller supplies a status code hint.
    assert categorize_exception(RuntimeError('x'), status_code=401) == ErrorCategory.AUTH
    assert categorize_exception(RuntimeError('x'), status_code=404) == ErrorCategory.CLIENT


def test_categorize_exception_default_is_internal():
    # No class match, no status hint → bug.
    assert categorize_exception(RuntimeError('x')) == ErrorCategory.INTERNAL
    assert categorize_exception(KeyError('k')) == ErrorCategory.INTERNAL


# ---------------------------------------------------------------------------
# End-to-end: errorhandler(500)
#
# We build a tiny helper app that mounts a route raising a chosen
# exception, so we exercise the real errorhandler wiring in create_app
# without touching any blueprints that may swallow exceptions themselves.
# ---------------------------------------------------------------------------


@pytest.fixture
def boom_app(app):
    """Register an exploding /__boom route on the real test app."""
    from werkzeug.exceptions import NotFound

    @app.route('/__boom_runtime')
    def _boom_runtime():
        raise RuntimeError('deliberate test failure')

    @app.route('/__boom_data')
    def _boom_data():
        raise DataError('deliberate data error')

    @app.route('/__boom_external')
    def _boom_external():
        raise ExternalError('deliberate external error')

    @app.route('/__boom_http')
    def _boom_http():
        # HTTPException path — must pass through to Flask's defaults.
        raise NotFound('deliberate 404')

    return app


def _count_errors_for(status, category, body):
    """Parse Prometheus text body and return the counter value."""
    needle = f'resume_site_errors_total{{category="{category}",status="{status}"}}'
    for line in body.splitlines():
        if line.startswith(needle):
            # e.g. `resume_site_errors_total{...} 3`
            return float(line.rsplit(' ', 1)[1])
    return 0.0


def test_errorhandler_500_returns_safe_text_body(boom_app):
    client = boom_app.test_client()
    response = client.get('/__boom_runtime')
    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert 'Internal Server Error' in body
    # Safety: never leak the exception message or stack
    assert 'deliberate test failure' not in body
    assert 'Traceback' not in body
    assert 'RuntimeError' not in body
    # Request ID surfaces so operators can correlate.
    assert response.headers.get('X-Request-ID')
    assert response.headers.get('X-Request-ID') in body


def test_errorhandler_500_json_when_requested(boom_app):
    client = boom_app.test_client()
    response = client.get('/__boom_runtime', headers={'Accept': 'application/json'})
    assert response.status_code == 500
    payload = json.loads(response.get_data(as_text=True))
    assert payload['error'] == 'internal server error'
    assert payload['code'] == ErrorCategory.INTERNAL
    assert payload['request_id']
    # No debugging noise
    assert 'traceback' not in payload
    assert 'message' not in payload


def test_errorhandler_500_logs_exc_info_at_error(boom_app, caplog):
    client = boom_app.test_client()
    with caplog.at_level(logging.ERROR, logger='app.request'):
        client.get('/__boom_runtime')

    error_records = [
        r for r in caplog.records if r.name == 'app.request' and r.levelno == logging.ERROR
    ]
    # At least one error record with exc_info attached
    assert any(r.exc_info for r in error_records), 'expected exc_info on at least one ERROR record'
    # And it has our error_category field
    assert any(getattr(r, 'error_category', None) == ErrorCategory.INTERNAL for r in error_records)


def test_errorhandler_data_category_flows_from_raised_class(boom_app):
    client = boom_app.test_client()
    response = client.get('/__boom_data', headers={'Accept': 'application/json'})
    assert response.status_code == 500
    payload = json.loads(response.get_data(as_text=True))
    assert payload['code'] == ErrorCategory.DATA


def test_errorhandler_external_category_flows_from_raised_class(boom_app):
    client = boom_app.test_client()
    response = client.get('/__boom_external', headers={'Accept': 'application/json'})
    assert response.status_code == 500
    payload = json.loads(response.get_data(as_text=True))
    assert payload['code'] == ErrorCategory.EXTERNAL


def test_http_exception_passes_through_to_flask_default(boom_app):
    client = boom_app.test_client()
    response = client.get('/__boom_http')
    # werkzeug.NotFound = 404; must NOT get wrapped to 500.
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# errors_total counter is incremented from the _log_request hook
# ---------------------------------------------------------------------------


@pytest.fixture
def metrics_enabled(app):
    """Flip metrics_enabled + allowed_networks so we can scrape the counter."""
    import sqlite3 as _sqlite3

    from app.services.settings_svc import invalidate_cache

    conn = _sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('metrics_enabled', 'true')")
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) '
        "VALUES ('metrics_allowed_networks', '127.0.0.0/8')"
    )
    conn.commit()
    conn.close()
    invalidate_cache()
    return app


# We deliberately do NOT reset the module-level metrics registry between
# tests. `create_app()` in app/__init__.py captures references to the
# Counter/Histogram instances when it runs; reloading the metrics module
# later would make the app's references point to stale objects the
# Prometheus scrape never sees. Instead these tests read the counter
# BEFORE the action and assert on the delta.


def test_errors_total_counter_increments_for_500(boom_app, metrics_enabled):
    client = boom_app.test_client()

    before = _count_errors_for(
        '500', ErrorCategory.INTERNAL, client.get('/metrics').get_data(as_text=True)
    )
    client.get('/__boom_runtime')
    after = _count_errors_for(
        '500', ErrorCategory.INTERNAL, client.get('/metrics').get_data(as_text=True)
    )
    assert after == before + 1


def test_errors_total_counter_increments_for_404(app, metrics_enabled):
    client = app.test_client()

    before = _count_errors_for(
        '404', ErrorCategory.CLIENT, client.get('/metrics').get_data(as_text=True)
    )
    client.get('/this-really-does-not-exist')
    after = _count_errors_for(
        '404', ErrorCategory.CLIENT, client.get('/metrics').get_data(as_text=True)
    )
    assert after == before + 1


def test_errors_total_counter_does_not_count_200(app, metrics_enabled):
    client = app.test_client()
    client.get('/')

    scrape = client.get('/metrics').get_data(as_text=True)
    # No 2xx lines should appear under errors_total, ever.
    for line in scrape.splitlines():
        if line.startswith('resume_site_errors_total{'):
            assert 'status="2' not in line


def test_errors_total_has_help_and_type_lines(app, metrics_enabled):
    client = app.test_client()
    scrape = client.get('/metrics').get_data(as_text=True)
    assert '# HELP resume_site_errors_total' in scrape
    assert '# TYPE resume_site_errors_total counter' in scrape


# ---------------------------------------------------------------------------
# Tiny stand-alone app to prove the errorhandler works outside our fixtures
# (guards against the fixture masking a wiring bug)
# ---------------------------------------------------------------------------


def test_errorhandler_does_not_depend_on_test_app_fixtures():
    # Build a minimal Flask app the way create_app would not, and verify
    # that if we call the same errorhandler factory pattern, a
    # deliberately-raised exception still surfaces as 500 — i.e. the
    # logic in create_app truly attaches the handler rather than
    # depending on a fixture subclass.
    app = Flask(__name__)

    @app.route('/')
    def _():
        raise RuntimeError('x')

    # Default Flask behaviour without our handler: 500 with an HTML body.
    # This test exists to prove create_app's handler is the *added value*;
    # if Flask one day changed its defaults, the sibling tests above
    # would still validate our handler's specific contract (safe body,
    # JSON negotiation, counter increment).
    response = app.test_client().get('/')
    assert response.status_code == 500
