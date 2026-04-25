"""
Deprecation Decorator Tests — Phase 37.2

Covers ``app.services.deprecation.deprecated`` plus the matching
webhook envelope plumbing in ``app.services.webhooks._build_envelope``.

Each test wires the decorator onto a throwaway view registered via a
small Blueprint on the existing ``app`` fixture, then drives it with
the standard test client. The metric counter is read straight off
``deprecated_api_calls_total.samples()`` to confirm the per-endpoint
label increments, and the INFO log line is captured via pytest's
``caplog`` fixture.

No real network traffic; the webhook envelope test exercises the
internal ``_build_envelope`` helper directly so we don't have to spin
up a fake HTTP server.
"""

from __future__ import annotations

import json
import logging

import pytest
from flask import Blueprint, jsonify

from app.services.deprecation import _to_http_date, deprecated
from app.services.metrics import deprecated_api_calls_total
from app.services.webhooks import _build_envelope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counter_value(endpoint_name):
    """Return the current counter value for one endpoint label, or 0 if absent."""
    for _name, labels, value in deprecated_api_calls_total.samples():
        if labels.get('endpoint') == endpoint_name:
            return value
    return 0


@pytest.fixture
def deprecated_route_app(app):
    """Register a tiny throwaway Blueprint with a deprecated route.

    Yields ``(app, endpoint_name)`` so the test can drive a request and
    later look up the counter value by the same endpoint name.
    """
    bp = Blueprint('test_deprecation', __name__)

    @bp.route('/__test_deprecated')
    @deprecated(sunset_date='2030-01-01')
    def deprecated_view():
        return jsonify(ok=True)

    app.register_blueprint(bp)
    yield app, 'deprecated_view'


# ---------------------------------------------------------------------------
# HTTP-date helper
# ---------------------------------------------------------------------------


def test_to_http_date_emits_rfc7231_format():
    # email.utils.format_datetime with usegmt=True ends in ' GMT' and
    # uses C-locale day/month names.
    got = _to_http_date('2030-01-01')
    assert got.endswith(' GMT')
    assert 'Tue, 01 Jan 2030' in got


# ---------------------------------------------------------------------------
# Decorator: headers
# ---------------------------------------------------------------------------


def test_deprecated_sets_three_headers(deprecated_route_app):
    app, _endpoint = deprecated_route_app
    client = app.test_client()
    resp = client.get('/__test_deprecated')
    assert resp.status_code == 200
    assert resp.headers.get('Deprecation') == 'true'
    sunset = resp.headers.get('Sunset')
    assert sunset is not None
    # Phase 37.2 — Sunset must be an RFC 7231 HTTP-date, not the raw
    # ISO date string.
    assert sunset.endswith(' GMT')
    assert 'Jan 2030' in sunset


def test_deprecated_with_replacement_sets_link_header(app):
    bp = Blueprint('test_deprecation_link', __name__)

    @bp.route('/__test_deprecated_link')
    @deprecated(sunset_date='2030-01-01', replacement='/api/v2/posts')
    def view():
        return jsonify(ok=True)

    app.register_blueprint(bp)
    client = app.test_client()
    resp = client.get('/__test_deprecated_link')
    assert resp.status_code == 200
    link = resp.headers.get('Link')
    assert link is not None
    assert '/api/v2/posts' in link
    assert 'rel="successor-version"' in link


def test_deprecated_without_replacement_omits_link_header(deprecated_route_app):
    app, _endpoint = deprecated_route_app
    client = app.test_client()
    resp = client.get('/__test_deprecated')
    assert resp.headers.get('Link') is None


# ---------------------------------------------------------------------------
# Decorator: logging
# ---------------------------------------------------------------------------


def test_deprecated_logs_info_on_app_api_deprecation(deprecated_route_app, caplog):
    app, _endpoint = deprecated_route_app
    client = app.test_client()
    with caplog.at_level(logging.INFO, logger='app.api.deprecation'):
        resp = client.get(
            '/__test_deprecated',
            headers={'User-Agent': 'pytest-deprecation/1.0', 'X-Client-ID': 'consumer-42'},
        )
    assert resp.status_code == 200

    matching = [r for r in caplog.records if r.name == 'app.api.deprecation']
    assert matching, 'expected at least one INFO log on app.api.deprecation'
    assert any(r.levelno == logging.INFO for r in matching)
    # The user-agent and X-Client-ID make it into the message so
    # operators can identify the caller.
    rendered = ' '.join(r.getMessage() for r in matching)
    assert 'pytest-deprecation/1.0' in rendered
    assert 'consumer-42' in rendered


# ---------------------------------------------------------------------------
# Decorator: counter
# ---------------------------------------------------------------------------


def test_deprecated_increments_counter_per_call(deprecated_route_app):
    app, endpoint = deprecated_route_app
    client = app.test_client()
    before = _counter_value(endpoint)
    client.get('/__test_deprecated')
    after_one = _counter_value(endpoint)
    client.get('/__test_deprecated')
    after_two = _counter_value(endpoint)
    assert after_one == before + 1
    assert after_two == before + 2


# ---------------------------------------------------------------------------
# Decorator: idempotent stacking
# ---------------------------------------------------------------------------


def test_stacked_deprecated_decorators_do_not_double_set_header(app, caplog):
    """When @deprecated wraps a route already wearing @deprecated, the
    outer decorator must no-op: the inner wrapper runs first and stamps
    the headers + counter + log, the outer wrapper sees Deprecation
    already set and bows out so we don't double-count or overwrite."""
    bp = Blueprint('test_deprecation_stack', __name__)

    @bp.route('/__test_deprecated_stack')
    @deprecated(sunset_date='2030-01-01', replacement='/api/v2/outer')
    @deprecated(sunset_date='2031-06-15', replacement='/api/v2/inner')
    def view():
        return jsonify(ok=True)

    app.register_blueprint(bp)
    client = app.test_client()
    before = _counter_value('view')

    with caplog.at_level(logging.INFO, logger='app.api.deprecation'):
        resp = client.get('/__test_deprecated_stack')

    assert resp.status_code == 200
    # Inner wrapper executes first and writes the headers; the outer
    # wrapper finds ``Deprecation`` already set and skips its own
    # writes — so the visible values are the inner decorator's.
    assert resp.headers.get('Deprecation') == 'true'
    assert '/api/v2/inner' in resp.headers.get('Link', '')
    # Sunset matches the inner (2031), not the outer (2030).
    assert 'Jun 2031' in resp.headers.get('Sunset', '')

    # Exactly one INFO log line, exactly one counter bump — proof the
    # outer wrapper truly no-op'd.
    matching = [r for r in caplog.records if r.name == 'app.api.deprecation']
    assert len(matching) == 1
    after = _counter_value('view')
    assert after == before + 1


# ---------------------------------------------------------------------------
# Webhook envelope plumbing
# ---------------------------------------------------------------------------


def test_webhook_envelope_injects_deprecated_and_sunset_keys():
    body = _build_envelope(
        'blog.published',
        {'post_id': 42},
        deprecated=True,
        sunset='2030-01-01',
    )
    envelope = json.loads(body)
    assert envelope['event'] == 'blog.published'
    assert envelope['data']['post_id'] == 42
    assert envelope['data']['deprecated'] is True
    assert envelope['data']['sunset'] == '2030-01-01'


def test_webhook_envelope_omits_keys_when_not_deprecated():
    body = _build_envelope('blog.published', {'post_id': 42})
    envelope = json.loads(body)
    assert 'deprecated' not in envelope['data']
    assert 'sunset' not in envelope['data']


def test_webhook_envelope_does_not_mutate_caller_payload():
    """The caller's payload dict must not gain ``deprecated`` / ``sunset``
    keys as a side effect — webhook fan-out hands the same payload
    object to multiple subscribers."""
    payload = {'post_id': 42}
    _build_envelope('blog.published', payload, deprecated=True, sunset='2030-01-01')
    assert 'deprecated' not in payload
    assert 'sunset' not in payload
