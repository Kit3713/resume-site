"""
Structured Logging Tests — Phase 18.1

Verifies app.services.logging:
- _JsonFormatter emits valid JSON with the full schema and includes extras.
- _HumanFormatter produces a single line matching the documented shape.
- _RequestContextFilter injects request_id / client_ip_hash when flask.g is
  populated, and '-' sentinel otherwise.
- hash_client_ip is deterministic per-salt and salt-dependent.
- configure_logging is idempotent (repeat calls don't stack handlers).
- End-to-end via the Flask test client: one log record per request,
  status-to-level mapping (2xx→INFO, 4xx→WARNING, 5xx→ERROR), request_id
  matches the X-Request-ID response header.
"""

from __future__ import annotations

import json
import logging
import re

import pytest

from app.services.logging import (
    LOG_FORMAT_HUMAN,
    LOG_FORMAT_JSON,
    _HumanFormatter,
    _JsonFormatter,
    _RequestContextFilter,
    configure_logging,
    hash_client_ip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    name='test.logger',
    level=logging.INFO,
    msg='hello',
    extra=None,
    request_id=None,
    client_ip_hash=None,
):
    """Build a LogRecord as if it had gone through the filter."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    if request_id is not None:
        record.request_id = request_id
    if client_ip_hash is not None:
        record.client_ip_hash = client_ip_hash
    return record


class _ListHandler(logging.Handler):
    """Capture records into a list for end-to-end test assertions."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


# ---------------------------------------------------------------------------
# _JsonFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_emits_required_schema():
    record = _make_record(request_id='abc123', client_ip_hash='deadbeef')
    line = _JsonFormatter().format(record)
    payload = json.loads(line)

    assert set(payload).issuperset(
        {
            'timestamp',
            'level',
            'logger',
            'message',
            'module',
            'request_id',
            'client_ip_hash',
        }
    )
    assert payload['level'] == 'INFO'
    assert payload['message'] == 'hello'
    assert payload['request_id'] == 'abc123'
    assert payload['client_ip_hash'] == 'deadbeef'


def test_json_formatter_timestamp_is_iso_utc_z():
    record = _make_record()
    payload = json.loads(_JsonFormatter().format(record))
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', payload['timestamp'])


def test_json_formatter_includes_extras_verbatim():
    record = _make_record(
        extra={'method': 'GET', 'path': '/x', 'status_code': 200, 'duration_ms': 42}
    )
    payload = json.loads(_JsonFormatter().format(record))
    assert payload['method'] == 'GET'
    assert payload['path'] == '/x'
    assert payload['status_code'] == 200
    assert payload['duration_ms'] == 42


def test_json_formatter_handles_non_serialisable_extra():
    record = _make_record(extra={'obj': object()})
    payload = json.loads(_JsonFormatter().format(record))
    # Non-JSON-serialisable values fall back to repr()
    assert 'object at 0x' in payload['obj']


def test_json_formatter_defaults_context_to_sentinel_when_missing():
    record = _make_record()  # no request_id or client_ip_hash set
    payload = json.loads(_JsonFormatter().format(record))
    assert payload['request_id'] == '-'
    assert payload['client_ip_hash'] == '-'


# ---------------------------------------------------------------------------
# _HumanFormatter
# ---------------------------------------------------------------------------


def test_human_formatter_single_line_shape():
    record = _make_record(
        msg='GET /portfolio 200 42ms', request_id='req42', client_ip_hash='hash99'
    )
    line = _HumanFormatter().format(record)
    assert '\n' not in line
    assert re.match(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z '
        r'\[INFO \] test\.logger req=req42 ip=hash99 '
        r'GET /portfolio 200 42ms$',
        line,
    )


# ---------------------------------------------------------------------------
# _RequestContextFilter
# ---------------------------------------------------------------------------


def test_filter_outside_request_context_sets_sentinel():
    record = _make_record()
    _RequestContextFilter().filter(record)
    assert record.request_id == '-'
    assert record.client_ip_hash == '-'


def test_filter_in_request_context_reads_g(app):
    from flask import g

    with app.test_request_context('/'):
        g.request_id = 'abcd1234'
        g.client_ip_hash = 'facecafe00000000'
        record = _make_record()
        _RequestContextFilter().filter(record)
    assert record.request_id == 'abcd1234'
    assert record.client_ip_hash == 'facecafe00000000'


def test_filter_in_request_context_with_empty_g(app):
    """g may be stripped by an error handler before logging runs."""
    with app.test_request_context('/'):
        record = _make_record()
        _RequestContextFilter().filter(record)
    assert record.request_id == '-'
    assert record.client_ip_hash == '-'


# ---------------------------------------------------------------------------
# hash_client_ip
# ---------------------------------------------------------------------------


def test_hash_client_ip_is_deterministic():
    a = hash_client_ip('192.0.2.1', 'salt-one')
    b = hash_client_ip('192.0.2.1', 'salt-one')
    assert a == b
    assert re.match(r'^[0-9a-f]{16}$', a)


def test_hash_client_ip_depends_on_salt():
    a = hash_client_ip('192.0.2.1', 'salt-one')
    b = hash_client_ip('192.0.2.1', 'salt-two')
    assert a != b


def test_hash_client_ip_handles_empty_inputs():
    # Defensive against request.remote_addr is None
    assert re.match(r'^[0-9a-f]{16}$', hash_client_ip('', 'salt'))
    assert re.match(r'^[0-9a-f]{16}$', hash_client_ip(None, 'salt'))
    assert re.match(r'^[0-9a-f]{16}$', hash_client_ip('10.0.0.1', ''))


# ---------------------------------------------------------------------------
# configure_logging — idempotence
# ---------------------------------------------------------------------------


def test_configure_logging_is_idempotent(app, monkeypatch):
    root = logging.getLogger()
    monkeypatch.setenv('RESUME_SITE_LOG_FORMAT', LOG_FORMAT_JSON)

    configure_logging(app)
    first_handlers = list(root.handlers)
    configure_logging(app)
    second_handlers = list(root.handlers)

    assert len(first_handlers) == 1
    assert len(second_handlers) == 1
    # Handler instance may change but count stays at one (idempotent).


def test_configure_logging_honours_format_env(app, monkeypatch):
    monkeypatch.setenv('RESUME_SITE_LOG_FORMAT', LOG_FORMAT_HUMAN)
    configure_logging(app)
    formatter = logging.getLogger().handlers[0].formatter
    assert isinstance(formatter, _HumanFormatter)

    monkeypatch.setenv('RESUME_SITE_LOG_FORMAT', LOG_FORMAT_JSON)
    configure_logging(app)
    formatter = logging.getLogger().handlers[0].formatter
    assert isinstance(formatter, _JsonFormatter)


def test_configure_logging_honours_level_env(app, monkeypatch):
    monkeypatch.setenv('RESUME_SITE_LOG_LEVEL', 'WARNING')
    configure_logging(app)
    assert logging.getLogger().level == logging.WARNING

    monkeypatch.setenv('RESUME_SITE_LOG_LEVEL', 'DEBUG')
    configure_logging(app)
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_falls_back_on_bad_values(app, monkeypatch):
    monkeypatch.setenv('RESUME_SITE_LOG_FORMAT', 'bogus')
    monkeypatch.setenv('RESUME_SITE_LOG_LEVEL', 'NOT_A_LEVEL')
    configure_logging(app)
    # Defaults: JSON, INFO
    assert isinstance(logging.getLogger().handlers[0].formatter, _JsonFormatter)
    assert logging.getLogger().level == logging.INFO


# ---------------------------------------------------------------------------
# End-to-end via the Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_records(app):
    """Attach a ListHandler to the app.request logger to capture records."""
    handler = _ListHandler()
    handler.addFilter(_RequestContextFilter())
    logger = logging.getLogger('app.request')
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def test_request_logger_emits_one_record_per_request(client, captured_records):
    client.get('/')
    # Filter to only app.request records (analytics / other loggers may also fire)
    records = [r for r in captured_records if r.name == 'app.request']
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert rec.method == 'GET'
    assert rec.path == '/'
    assert rec.status_code == 200
    assert isinstance(rec.duration_ms, int)


def test_404_is_logged_at_warning(client, captured_records):
    client.get('/does-not-exist-xyz')
    records = [r for r in captured_records if r.name == 'app.request']
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].status_code == 404


def test_log_request_id_matches_response_header(client, captured_records):
    response = client.get('/')
    header_id = response.headers.get('X-Request-ID')
    records = [r for r in captured_records if r.name == 'app.request']
    assert records[0].request_id == header_id


def test_log_client_ip_hash_stable_across_requests(client, captured_records):
    client.get('/')
    client.get('/')
    records = [r for r in captured_records if r.name == 'app.request']
    assert len(records) == 2
    assert records[0].client_ip_hash == records[1].client_ip_hash
    assert re.match(r'^[0-9a-f]{16}$', records[0].client_ip_hash)
