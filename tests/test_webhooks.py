"""
Webhook Service Tests — Phase 19.2

Covers ``app.services.webhooks``:

* HMAC signing — stable across input forms, matches what a downstream
  verifier should compute.
* Sync delivery — happy path, HTTP error, network error / timeout. All
  failure modes captured in the returned :class:`DeliveryResult`
  rather than raising.
* CRUD — create / get / list / update / delete and the
  ``list_enabled_subscribers`` helper used by the dispatcher.
* Auto-disable — ``increment_failures`` flips ``enabled`` to 0 once
  the consecutive-failure counter crosses the configured threshold;
  ``reset_failures`` zeros it on the next 2xx.
* Async dispatch — ``dispatch_event_async`` spawns one daemon thread
  per matching enabled webhook; threads use fresh DB connections.
* Bus integration — handlers registered by ``register_bus_handlers``
  short-circuit when ``webhooks_enabled = false`` and otherwise fan
  out to ``dispatch_event_async``.

No real network traffic — every delivery test patches
``app.services.webhooks.urlopen`` with a controlled fake. Tests use
the standard ``app`` / ``populated_db`` fixtures from
``tests/conftest.py``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

import app.events as events_mod
from app.services.webhooks import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    DeliveryResult,
    Webhook,
    create_webhook,
    delete_webhook,
    deliver_now,
    dispatch_event_async,
    get_webhook,
    increment_failures,
    list_enabled_subscribers,
    list_recent_deliveries,
    list_webhooks,
    purge_old_deliveries,
    record_delivery,
    register_bus_handlers,
    reset_failures,
    sign_payload,
    update_webhook,
)

# ---------------------------------------------------------------------------
# Bus isolation — every webhook test starts with an empty registry.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_bus():
    events_mod.clear()
    yield
    events_mod.clear()


# ---------------------------------------------------------------------------
# DB fixture — reuse the conftest `app` fixture's tmp DB path.
# ---------------------------------------------------------------------------


@pytest.fixture
def db(app):
    """Return an open sqlite3 Connection to the test DB."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Stub HTTP responses — drop-in for urlopen()
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal context-manager stand-in for the HTTPResponse urlopen returns."""

    def __init__(self, status=200):
        self.status = status

    def getcode(self):
        return self.status

    def read(self):
        return b''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_sign_payload_returns_hex_sha256():
    sig = sign_payload('shh', b'hello')
    # 64 hex chars = SHA-256 hex digest.
    assert len(sig) == 64
    assert all(c in '0123456789abcdef' for c in sig)


def test_sign_payload_accepts_str_and_bytes_interchangeably():
    assert sign_payload('shh', 'hello') == sign_payload(b'shh', b'hello')


def test_sign_payload_is_stable_across_calls():
    a = sign_payload('shh', b'hello')
    b = sign_payload('shh', b'hello')
    assert a == b


def test_sign_payload_diverges_on_secret_or_body_change():
    base = sign_payload('shh', b'hello')
    assert sign_payload('shh', b'hellp') != base
    assert sign_payload('shi', b'hello') != base


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_get_roundtrip(db):
    wh_id = create_webhook(
        db, name='Slack', url='https://example/h', secret='shh', events=['blog.published']
    )
    fetched = get_webhook(db, wh_id)
    assert isinstance(fetched, Webhook)
    assert fetched.name == 'Slack'
    assert fetched.url == 'https://example/h'
    assert fetched.events == ['blog.published']
    assert fetched.enabled is True
    assert fetched.failure_count == 0


def test_get_unknown_id_returns_none(db):
    assert get_webhook(db, 9999) is None


def test_events_string_input_normalised_to_list(db):
    wh_id = create_webhook(
        db, name='X', url='https://e', secret='s', events='blog.published, contact.submitted'
    )
    wh = get_webhook(db, wh_id)
    assert wh.events == ['blog.published', 'contact.submitted']


def test_events_json_string_input_normalised(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events='["a","b"]')
    assert get_webhook(db, wh_id).events == ['a', 'b']


def test_list_webhooks_newest_first(db):
    create_webhook(db, name='one', url='https://e/1', secret='s')
    create_webhook(db, name='two', url='https://e/2', secret='s')
    rows = list_webhooks(db)
    assert [r.name for r in rows] == ['two', 'one']


def test_list_enabled_subscribers_filters_by_event_and_enabled(db):
    create_webhook(db, name='wildcard', url='https://e/w', secret='s', events=['*'])
    create_webhook(db, name='blog', url='https://e/b', secret='s', events=['blog.published'])
    create_webhook(
        db, name='off', url='https://e/o', secret='s', events=['blog.published'], enabled=False
    )

    matches = list_enabled_subscribers(db, 'blog.published')
    names = sorted(m.name for m in matches)
    # Wildcard + the blog-only subscriber match; the disabled row is filtered.
    assert names == ['blog', 'wildcard']


def test_list_enabled_subscribers_skips_non_matching_events(db):
    create_webhook(db, name='blog', url='https://e/b', secret='s', events=['blog.published'])
    matches = list_enabled_subscribers(db, 'contact.submitted')
    assert matches == []


def test_update_webhook_only_writes_known_fields(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    update_webhook(db, wh_id, name='Y', url='https://e2', unknown='ignored')
    wh = get_webhook(db, wh_id)
    assert wh.name == 'Y'
    assert wh.url == 'https://e2'


def test_update_webhook_normalises_events(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    update_webhook(db, wh_id, events='a, b, c')
    assert get_webhook(db, wh_id).events == ['a', 'b', 'c']


def test_update_webhook_coerces_enabled_to_int(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', enabled=True)
    update_webhook(db, wh_id, enabled=False)
    assert get_webhook(db, wh_id).enabled is False


def test_delete_cascades_to_deliveries(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 200, 12, ''))
    assert list_recent_deliveries(db, webhook_id=wh_id)
    delete_webhook(db, wh_id)
    assert list_recent_deliveries(db, webhook_id=wh_id) == []


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------


def test_record_delivery_writes_row_and_updates_last_triggered(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'blog.published', 200, 42, ''))

    deliveries = list_recent_deliveries(db, webhook_id=wh_id)
    assert len(deliveries) == 1
    assert deliveries[0]['event'] == 'blog.published'
    assert deliveries[0]['status_code'] == 200
    assert deliveries[0]['response_time_ms'] == 42

    wh = get_webhook(db, wh_id)
    assert wh.last_triggered_at  # populated, ISO-8601


def test_record_delivery_truncates_long_error_messages(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    long_error = 'x' * 5000
    record_delivery(db, DeliveryResult(wh_id, 'evt', 0, 0, long_error))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert len(row['error_message']) == 500  # _ERROR_MESSAGE_LIMIT


def test_purge_old_deliveries(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    # Insert one fresh and one stale row directly so we can pin created_at.
    db.execute(
        'INSERT INTO webhook_deliveries (webhook_id, event, status_code, created_at) '
        "VALUES (?, 'old', 200, strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-60 days'))",
        (wh_id,),
    )
    db.execute(
        'INSERT INTO webhook_deliveries (webhook_id, event, status_code) VALUES (?, ?, ?)',
        (wh_id, 'new', 200),
    )
    db.commit()
    deleted = purge_old_deliveries(db, keep_days=30)
    assert deleted == 1
    remaining = [d['event'] for d in list_recent_deliveries(db, webhook_id=wh_id)]
    assert remaining == ['new']


# ---------------------------------------------------------------------------
# Auto-disable
# ---------------------------------------------------------------------------


def test_increment_failures_disables_at_threshold(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    for _ in range(2):
        disabled = increment_failures(db, wh_id, threshold=3)
        assert disabled is False
    disabled = increment_failures(db, wh_id, threshold=3)
    assert disabled is True
    assert get_webhook(db, wh_id).enabled is False
    assert get_webhook(db, wh_id).failure_count == 3


def test_increment_failures_threshold_zero_never_disables(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    for _ in range(20):
        assert increment_failures(db, wh_id, threshold=0) is False
    assert get_webhook(db, wh_id).enabled is True
    assert get_webhook(db, wh_id).failure_count == 20


def test_reset_failures_zeros_counter_after_increments(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    increment_failures(db, wh_id, threshold=10)
    increment_failures(db, wh_id, threshold=10)
    reset_failures(db, wh_id)
    assert get_webhook(db, wh_id).failure_count == 0


# ---------------------------------------------------------------------------
# deliver_now — happy + failure paths
# ---------------------------------------------------------------------------


def _wh(**overrides):
    """Build a Webhook record without going through the DB."""
    base = {
        'id': 1,
        'name': 'test',
        'url': 'https://example.test/h',
        'secret': 'shh',
        'events': ['*'],
        'enabled': True,
        'failure_count': 0,
        'created_at': '2026-01-01T00:00:00Z',
        'last_triggered_at': None,
    }
    base.update(overrides)
    return Webhook(**base)


def test_deliver_now_happy_path_sends_signed_post(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['url'] = request.full_url
        captured['method'] = request.get_method()
        captured['data'] = request.data
        captured['headers'] = dict(request.header_items())
        captured['timeout'] = timeout
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)

    result = deliver_now(_wh(), 'blog.published', {'post_id': 42}, timeout=3)

    assert result.status_code == 200
    assert result.error == ''
    assert result.event == 'blog.published'

    # Body is a JSON envelope with stable key order.
    envelope = json.loads(captured['data'])
    assert envelope['event'] == 'blog.published'
    assert envelope['data'] == {'post_id': 42}
    assert envelope['timestamp']  # ISO-8601 string

    # Signature header matches what an external verifier would compute.
    expected_sig = sign_payload('shh', captured['data'])
    headers = {k.lower(): v for k, v in captured['headers'].items()}
    assert headers[SIGNATURE_HEADER.lower()] == expected_sig
    assert headers[EVENT_HEADER.lower()] == 'blog.published'
    assert headers['content-type'] == 'application/json'
    assert captured['timeout'] == 3
    assert captured['method'] == 'POST'


def test_deliver_now_records_http_error_status(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 503, 'Service Unavailable', hdrs=None, fp=BytesIO(b''))

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)

    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 503
    assert 'HTTP 503' in result.error
    assert 'Service Unavailable' in result.error


def test_deliver_now_records_url_error_as_status_zero(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise URLError('Name or service not known')

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)

    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 0
    assert 'URLError' in result.error
    assert 'Name or service not known' in result.error


def test_deliver_now_records_timeout_as_status_zero(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise TimeoutError('timed out')

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)

    result = deliver_now(_wh(), 'evt', {}, timeout=1)
    assert result.status_code == 0
    assert 'TimeoutError' in result.error


def test_deliver_now_envelope_uses_sorted_keys(monkeypatch):
    """Stable envelope serialisation is what makes signature verification reproducible."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    deliver_now(_wh(), 'evt', {'b': 2, 'a': 1, 'c': 3})

    body = captured['data'].decode('utf-8')
    # `data` must appear with sorted inner keys too.
    assert '"data": {"a": 1, "b": 2, "c": 3}' in body


# ---------------------------------------------------------------------------
# dispatch_event_async — fan-out integration
# ---------------------------------------------------------------------------


def test_dispatch_event_async_records_one_delivery_per_matching_webhook(monkeypatch, app, db):
    create_webhook(db, name='wild', url='https://e/w', secret='s', events=['*'])
    create_webhook(db, name='blog', url='https://e/b', secret='s', events=['blog.published'])
    create_webhook(db, name='other', url='https://e/o', secret='s', events=['contact.submitted'])

    monkeypatch.setattr(
        'app.services.webhooks.urlopen', lambda req, timeout=None: _StubResponse(200)
    )

    threads = dispatch_event_async(
        app.config['DATABASE_PATH'],
        'blog.published',
        {'post_id': 1},
        _join_for_tests=True,
    )
    assert len(threads) == 2  # wildcard + blog match; 'other' filtered

    # Re-open the DB to see writes from the worker threads.
    fresh = sqlite3.connect(app.config['DATABASE_PATH'])
    fresh.row_factory = sqlite3.Row
    try:
        rows = fresh.execute(
            'SELECT webhook_id, event, status_code FROM webhook_deliveries ORDER BY id'
        ).fetchall()
    finally:
        fresh.close()
    assert len(rows) == 2
    assert {r['event'] for r in rows} == {'blog.published'}
    assert {r['status_code'] for r in rows} == {200}


def test_dispatch_event_async_skips_disabled_webhooks(monkeypatch, app, db):
    create_webhook(db, name='off', url='https://e/o', secret='s', events=['*'], enabled=False)
    monkeypatch.setattr(
        'app.services.webhooks.urlopen', lambda req, timeout=None: _StubResponse(200)
    )

    threads = dispatch_event_async(
        app.config['DATABASE_PATH'], 'blog.published', {}, _join_for_tests=True
    )
    assert threads == []


def test_dispatch_event_async_disables_after_repeated_failures(monkeypatch, app, db):
    wh_id = create_webhook(db, name='flaky', url='https://e/f', secret='s', events=['*'])

    def boom(req, timeout=None):
        raise URLError('down')

    monkeypatch.setattr('app.services.webhooks.urlopen', boom)

    for _ in range(3):
        dispatch_event_async(
            app.config['DATABASE_PATH'],
            'evt',
            {},
            timeout=1,
            threshold=3,
            _join_for_tests=True,
        )

    fresh = sqlite3.connect(app.config['DATABASE_PATH'])
    fresh.row_factory = sqlite3.Row
    try:
        wh = get_webhook(fresh, wh_id)
    finally:
        fresh.close()
    assert wh.failure_count == 3
    assert wh.enabled is False


def test_dispatch_event_async_resets_failure_count_on_success(monkeypatch, app, db):
    wh_id = create_webhook(db, name='flap', url='https://e/f', secret='s', events=['*'])
    increment_failures(db, wh_id, threshold=10)
    increment_failures(db, wh_id, threshold=10)
    assert get_webhook(db, wh_id).failure_count == 2

    monkeypatch.setattr(
        'app.services.webhooks.urlopen', lambda req, timeout=None: _StubResponse(204)
    )

    dispatch_event_async(app.config['DATABASE_PATH'], 'evt', {}, _join_for_tests=True)

    fresh = sqlite3.connect(app.config['DATABASE_PATH'])
    fresh.row_factory = sqlite3.Row
    try:
        assert get_webhook(fresh, wh_id).failure_count == 0
    finally:
        fresh.close()


def test_dispatch_event_async_subscriber_lookup_failure_returns_empty():
    """Non-existent DB path must not raise — fail-open is the contract."""
    threads = dispatch_event_async('/no/such/db/file.sqlite', 'evt', {}, _join_for_tests=True)
    assert threads == []


def test_async_threads_are_daemon(monkeypatch, app, db):
    create_webhook(db, name='X', url='https://e/x', secret='s', events=['*'])
    # Block urlopen until the test releases it so the thread is observably alive.
    release = threading.Event()

    def slow_urlopen(req, timeout=None):
        release.wait(timeout=2)
        return _StubResponse(200)

    monkeypatch.setattr('app.services.webhooks.urlopen', slow_urlopen)

    threads = dispatch_event_async(app.config['DATABASE_PATH'], 'evt', {})
    try:
        assert all(t.daemon for t in threads), 'workers must be daemon so process can exit cleanly'
    finally:
        release.set()
        for t in threads:
            t.join(timeout=3)


# ---------------------------------------------------------------------------
# Bus integration
# ---------------------------------------------------------------------------


def test_register_bus_handlers_short_circuits_when_disabled(monkeypatch, app):
    # webhooks_enabled defaults to false in the registry — verify the
    # handler does NOT spawn a delivery.
    spawn_calls = []
    monkeypatch.setattr(
        'app.services.webhooks.dispatch_event_async',
        lambda *a, **kw: spawn_calls.append((a, kw)) or [],
    )

    register_bus_handlers(app.config['DATABASE_PATH'])
    events_mod.emit('blog.published', post_id=1)
    assert spawn_calls == []


def test_register_bus_handlers_dispatches_when_enabled(monkeypatch, app):
    # Flip webhooks_enabled to true.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('webhooks_enabled', 'true')")
    conn.commit()
    conn.close()

    spawn_calls = []
    monkeypatch.setattr(
        'app.services.webhooks.dispatch_event_async',
        lambda *a, **kw: spawn_calls.append((a, kw)) or [],
    )

    register_bus_handlers(app.config['DATABASE_PATH'])
    events_mod.emit('blog.published', post_id=1, slug='hello')

    assert len(spawn_calls) == 1
    args, kwargs = spawn_calls[0]
    assert args[0] == app.config['DATABASE_PATH']
    assert args[1] == 'blog.published'
    assert args[2] == {'post_id': 1, 'slug': 'hello'}


def test_register_bus_handlers_subscribes_to_every_canonical_event(app):
    register_bus_handlers(app.config['DATABASE_PATH'])
    from app.events import Events, handler_count

    for name in Events.ALL:
        assert handler_count(name) >= 1, f'no webhook handler for {name!r}'


def test_handler_settings_snapshot_handles_bad_values(monkeypatch, app):
    """Garbage settings values must fall back to safe defaults, not crash."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.executemany(
        'INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)',
        [
            ('webhooks_enabled', 'true'),
            ('webhook_timeout_seconds', 'not-a-number'),
            ('webhook_failure_threshold', '-5'),
        ],
    )
    conn.commit()
    conn.close()

    spawn_calls = []
    monkeypatch.setattr(
        'app.services.webhooks.dispatch_event_async',
        lambda *a, **kw: spawn_calls.append((a, kw)) or [],
    )

    register_bus_handlers(app.config['DATABASE_PATH'])
    events_mod.emit('blog.published')

    # Dispatch still happens; bad values fall back to defaults.
    assert len(spawn_calls) == 1
    _, kwargs = spawn_calls[0]
    assert kwargs['timeout'] == 5  # default
    # Negative threshold gets clamped to 0 (auto-disable disabled).
    assert kwargs['threshold'] == 0


# ---------------------------------------------------------------------------
# Phase 22.3 — SSRF target validation
#
# validate_webhook_target() is the single gate used by admin-HTML,
# the JSON API, and (via re-resolution) the delivery worker. Every
# CIDR family listed in the audit issue #19 gets its own explicit
# rejection test so a regression in the block-list has an obvious
# failing case. DNS rebinding is exercised by monkeypatching
# _resolve_target_ips so one resolution returns public, the next
# returns private.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('url', 'tag'),
    [
        ('http://127.0.0.1/hook', 'loopback v4'),
        ('http://127.1.2.3/hook', 'loopback v4 (non-.1)'),
        ('http://[::1]/hook', 'loopback v6'),
        ('http://169.254.169.254/latest', 'link-local v4'),
        ('http://[fe80::1]/hook', 'link-local v6'),
        ('http://10.0.0.1/hook', 'RFC 1918 10/8'),
        ('http://172.20.1.1/hook', 'RFC 1918 172.16/12'),
        ('http://192.168.1.1/hook', 'RFC 1918 192.168/16'),
        ('http://100.64.0.1/hook', 'CGNAT 100.64/10'),
        ('http://[fc00::1]/hook', 'unique-local v6'),
        ('http://0.0.0.0/hook', 'this network'),
    ],
)
def test_validate_webhook_target_rejects_each_cidr_family(url, tag):
    from app.services.webhooks import validate_webhook_target

    ok, msg = validate_webhook_target(url, allow_private=False)
    assert ok is False, f'expected rejection for {tag} ({url}): {msg!r}'
    assert 'loopback / private / CGNAT' in msg


@pytest.mark.parametrize(
    'url',
    [
        'http://127.0.0.1/hook',
        'http://10.0.0.1/hook',
        'http://192.168.1.1/hook',
        'http://169.254.169.254/latest',
    ],
)
def test_validate_webhook_target_allows_private_when_opted_in(url):
    from app.services.webhooks import validate_webhook_target

    ok, msg = validate_webhook_target(url, allow_private=True)
    assert ok is True, f'allow_private=True should pass {url}: {msg!r}'
    assert msg == ''


def test_validate_webhook_target_rejects_non_http_scheme():
    from app.services.webhooks import validate_webhook_target

    ok, msg = validate_webhook_target('file:///etc/passwd', allow_private=False)
    assert ok is False
    assert 'http(s)' in msg


def test_validate_webhook_target_rejects_empty_url():
    from app.services.webhooks import validate_webhook_target

    ok, msg = validate_webhook_target('', allow_private=False)
    assert ok is False
    assert 'required' in msg


def test_validate_webhook_target_accepts_public_host(monkeypatch):
    """A host that resolves exclusively to public IPs must pass."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(
        webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.1', '2001:db8::1']
    )
    ok, msg = webhooks_mod.validate_webhook_target(
        'https://public.example.com/hook', allow_private=False
    )
    assert ok is True, msg


def test_validate_webhook_target_rejects_mixed_public_and_private(monkeypatch):
    """If ANY resolved IP is private, the whole URL is rejected.

    This is the DNS-rebinding staging case: attacker returns a public IP
    the operator can't object to alongside a private IP that the delivery
    worker would actually connect to. Rejecting on any-match closes the
    gap.
    """
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(
        webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.1', '10.0.0.1']
    )
    ok, msg = webhooks_mod.validate_webhook_target(
        'https://rebind.example.com/hook', allow_private=False
    )
    assert ok is False
    assert '10.0.0.1' in msg


def test_deliver_now_rebind_flips_to_private_between_create_and_delivery(monkeypatch):
    """With allow_private=False, a host that re-resolves to a private IP at
    delivery time aborts the request before urlopen fires. Simulates a
    DNS rebinding attack caught by the delivery-time re-validation."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['10.0.0.99'])

    called = []

    def fail_if_called(request, timeout=None):
        called.append(request.full_url)
        return _StubResponse(200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fail_if_called)

    result = deliver_now(
        _wh(url='https://rebind.example.com/hook'),
        'evt',
        {},
        timeout=2,
        allow_private=False,
    )
    assert result.status_code == 0
    assert 'SSRF guard' in result.error
    assert called == []  # urlopen never fired


def test_deliver_now_allow_private_true_skips_ssrf_check(monkeypatch):
    """When the operator explicitly allows private targets, the guard does
    not pre-empt the request — urlopen is called as usual."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['10.0.0.99'])
    sent = []

    def capture(request, timeout=None):
        sent.append(request.full_url)
        return _StubResponse(200)

    monkeypatch.setattr('app.services.webhooks.urlopen', capture)

    result = deliver_now(
        _wh(url='https://rebind.example.com/hook'),
        'evt',
        {},
        timeout=2,
        allow_private=True,
    )
    assert result.status_code == 200
    assert sent == ['https://rebind.example.com/hook']


# ---------------------------------------------------------------------------
# Phase 22.3 — No-follow redirects
#
# _NoRedirectHandler raises HTTPError on every 3xx so a compromised
# webhook target can't bounce delivery at an SSRF victim. Unit-test the
# handler directly (simulates what urlopen triggers), and assert that
# deliver_now records the 3xx status in its DeliveryResult.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('code', [301, 302, 303, 307, 308])
def test_no_redirect_handler_raises_on_each_redirect_status(code):
    from app.services.webhooks import _NoRedirectHandler

    handler = _NoRedirectHandler()
    # http_error_30x(req, fp, code, msg, headers) is the stdlib contract.
    fake_req = Webhook(
        id=0,
        name='x',
        url='https://example.test/',
        secret='',
        events=[],
        enabled=True,
        failure_count=0,
        created_at='',
        last_triggered_at=None,
    )

    class _FakeReq:
        full_url = 'https://example.test/'

    method = getattr(handler, f'http_error_{code}')
    with pytest.raises(HTTPError) as excinfo:
        method(_FakeReq(), BytesIO(b''), code, 'Moved', {})
    assert excinfo.value.code == code
    # We keep ``fake_req`` referenced so linters don't drop the imported
    # Webhook symbol (it's the shape the real delivery path passes).
    assert fake_req.id == 0


def test_deliver_now_records_redirect_as_failed_delivery(monkeypatch):
    """A 3xx response must land in the delivery log as a failure, never
    be silently followed. The module's urlopen goes through the custom
    OpenerDirector in production; for this unit test we simulate it by
    monkeypatching urlopen to raise HTTPError like _NoRedirectHandler
    would."""

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 302, 'Found', hdrs=None, fp=BytesIO(b''))

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)

    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 302
    assert 'HTTP 302' in result.error


def test_module_urlopen_is_the_no_redirect_wrapper():
    """Sanity check that our module-level urlopen is the opener-backed
    wrapper and not a leftover re-export of urllib.request.urlopen."""
    from urllib.request import urlopen as stdlib_urlopen

    from app.services import webhooks as webhooks_mod

    assert webhooks_mod.urlopen is not stdlib_urlopen
    assert webhooks_mod.urlopen.__module__ == 'app.services.webhooks'
