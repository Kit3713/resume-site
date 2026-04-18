"""
Webhook Dispatch Service — Phase 19.2

Subscribes to the Phase 19.1 event bus and POSTs each matching event to
every enabled :class:`Webhook` row, signed with HMAC-SHA256. Delivery is
asynchronous (one daemon thread per attempt) so a slow downstream never
stalls the request that emitted the event.

Design contract:

* **Stdlib only.** ``urllib.request`` for the POST, ``hmac`` /
  ``hashlib`` for the signature, ``threading.Thread`` for async
  fan-out. No new runtime dependency.
* **Fail-open.** Any exception inside the dispatcher is logged and
  swallowed — a misbehaving subscriber must never break the request
  that emitted the event. Mirrors the bus's own contract.
* **Single-process.** Daemon threads, no external queue, no retry
  beyond the auto-disable counter. The roadmap calls this out
  explicitly: high-volume deployments should put a real queue
  (RabbitMQ, Redis) in front of the bus, not retrofit one here.
* **Auto-disable.** ``failure_count`` increments on every non-2xx /
  network error and resets on the next 2xx. When it crosses the
  configured threshold the row's ``enabled`` flag flips to 0 and a
  WARNING gets logged; the admin UI shows the disabled state, and the
  operator must explicitly re-enable.
* **Per-thread DB connection.** Delivery threads open a fresh
  ``sqlite3.connect(db_path)`` because Flask's request-scoped
  connection lives on the wrong thread. The connection is closed in a
  ``finally`` so a thread that's killed mid-flight at process exit
  doesn't leak file descriptors.

Public surface (used by app factory + future admin UI / REST API):

* :func:`create_webhook`, :func:`list_webhooks`, :func:`get_webhook`,
  :func:`update_webhook`, :func:`delete_webhook`
* :func:`sign_payload` — HMAC-SHA256 hex digest, exported for any
  downstream verifier (e.g. test harnesses)
* :func:`deliver_now` — synchronous single-attempt POST
* :func:`dispatch_event_async` — fan-out entry point; what the bus
  handlers call
* :func:`register_bus_handlers` — registers one closure per
  ``Events.*`` constant; called once at app startup
* :func:`list_recent_deliveries`, :func:`purge_old_deliveries`
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from hashlib import sha256
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_log = logging.getLogger('app.webhooks')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: HTTP header carrying the hex HMAC-SHA256 signature of the body.
SIGNATURE_HEADER = 'X-Webhook-Signature'

#: HTTP header carrying the canonical event name.
EVENT_HEADER = 'X-Webhook-Event'

#: User-Agent string sent on every delivery — lets downstream services
#: filter our traffic in their access logs.
USER_AGENT = 'resume-site-webhooks/1.0'

#: Cap the error message we persist so a chatty downstream can't bloat
#: the deliveries table with a single multi-megabyte response body.
_ERROR_MESSAGE_LIMIT = 500


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Webhook(NamedTuple):
    """In-memory projection of one ``webhooks`` row.

    Use :func:`get_webhook` / :func:`list_webhooks` to obtain instances
    instead of constructing directly — the helpers parse the JSON
    ``events`` column into a Python list.
    """

    id: int
    name: str
    url: str
    secret: str
    events: list  # list of event names; ['*'] means all
    enabled: bool
    failure_count: int
    created_at: str
    last_triggered_at: str | None


class DeliveryResult(NamedTuple):
    """Outcome of one POST attempt — what gets recorded in the log."""

    webhook_id: int
    event: str
    status_code: int  # 0 for network error / timeout
    response_time_ms: int
    error: str  # empty string on success


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_webhook(row):
    """Convert a sqlite3.Row into a :class:`Webhook`.

    Defensive against a malformed ``events`` column — bad JSON falls
    back to an empty list (no events match) rather than raising at
    dispatch time and tripping the bus's fail-open.
    """
    if row is None:
        return None
    try:
        events = json.loads(row['events'])
        if not isinstance(events, list):
            events = []
    except (ValueError, TypeError):
        events = []
    return Webhook(
        id=row['id'],
        name=row['name'],
        url=row['url'],
        secret=row['secret'],
        events=events,
        enabled=bool(row['enabled']),
        failure_count=row['failure_count'],
        created_at=row['created_at'],
        last_triggered_at=row['last_triggered_at'],
    )


def _now_iso():
    """Current UTC time in the trailing-Z ISO-8601 form used everywhere else."""
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _normalise_events(events):
    """Coerce a JSON-parsed events value into a clean list of strings."""
    if isinstance(events, str):
        # Accept either a JSON-encoded array or a comma-separated string;
        # the admin UI may submit either depending on form widget choice.
        with contextlib.suppress(ValueError):
            parsed = json.loads(events)
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if str(e).strip()]
        return [e.strip() for e in events.split(',') if e.strip()]
    if isinstance(events, (list, tuple)):
        return [str(e).strip() for e in events if str(e).strip()]
    return []


def _matches(webhook, event_name):
    """Does ``webhook`` want to receive ``event_name``?"""
    if not webhook.enabled:
        return False
    return '*' in webhook.events or event_name in webhook.events


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_webhook(
    db: sqlite3.Connection,
    *,
    name: str,
    url: str,
    secret: str,
    events: list | tuple | str = ('*',),
    enabled: bool = True,
) -> int | None:
    """Insert a new webhook row. Returns the new id.

    ``events`` may be a list, tuple, or comma-separated / JSON string —
    :func:`_normalise_events` flattens them all to the canonical JSON
    array form before storage.
    """
    events_list = _normalise_events(events)
    cursor = db.execute(
        'INSERT INTO webhooks (name, url, secret, events, enabled) VALUES (?, ?, ?, ?, ?)',
        (name, url, secret, json.dumps(events_list), 1 if enabled else 0),
    )
    db.commit()
    return cursor.lastrowid


def get_webhook(db: sqlite3.Connection, webhook_id: int) -> Webhook | None:
    """Return one :class:`Webhook` or ``None``."""
    row = db.execute('SELECT * FROM webhooks WHERE id = ?', (webhook_id,)).fetchone()
    return _row_to_webhook(row)


def list_webhooks(db: sqlite3.Connection, *, include_disabled: bool = True) -> list[Webhook]:
    """Return every webhook, newest first."""
    if include_disabled:
        rows = db.execute('SELECT * FROM webhooks ORDER BY created_at DESC, id DESC').fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM webhooks WHERE enabled = 1 ORDER BY created_at DESC, id DESC'
        ).fetchall()
    return [_row_to_webhook(r) for r in rows]


def list_enabled_subscribers(db: sqlite3.Connection, event_name: str) -> list[Webhook]:
    """Return enabled webhooks subscribed to ``event_name``.

    Filters in Python rather than via SQL JSON functions so we don't
    depend on an SQLite build that ships ``json_each``. The
    ``enabled = 1`` index keeps the candidate set tiny.
    """
    rows = db.execute('SELECT * FROM webhooks WHERE enabled = 1').fetchall()
    return [w for w in (_row_to_webhook(r) for r in rows) if _matches(w, event_name)]


def update_webhook(db: sqlite3.Connection, webhook_id: int, **fields: object) -> None:
    """Update an arbitrary subset of columns. Unknown keys are ignored.

    The ``events`` field, if passed, is normalised before storage.
    """
    allowed = {'name', 'url', 'secret', 'events', 'enabled', 'failure_count', 'last_triggered_at'}
    sets = []
    params: list = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == 'events':
            value = json.dumps(_normalise_events(value))
        elif key == 'enabled':
            value = 1 if value else 0
        sets.append(f'{key} = ?')
        params.append(value)
    if not sets:
        return
    params.append(webhook_id)
    db.execute(f'UPDATE webhooks SET {", ".join(sets)} WHERE id = ?', params)  # noqa: S608  # nosec B608
    db.commit()


def delete_webhook(db: sqlite3.Connection, webhook_id: int) -> None:
    """Hard-delete the webhook (ON DELETE CASCADE drops its delivery log)."""
    db.execute('DELETE FROM webhooks WHERE id = ?', (webhook_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------


def record_delivery(db: sqlite3.Connection, result: DeliveryResult) -> None:
    """Persist a :class:`DeliveryResult` to ``webhook_deliveries`` and
    bump ``webhooks.last_triggered_at``.

    Both writes happen in the same connection so they cannot diverge
    even if a concurrent delivery thread is running against the same
    webhook.
    """
    db.execute(
        'INSERT INTO webhook_deliveries '
        '(webhook_id, event, status_code, response_time_ms, error_message) '
        'VALUES (?, ?, ?, ?, ?)',
        (
            result.webhook_id,
            result.event,
            result.status_code,
            result.response_time_ms,
            (result.error or '')[:_ERROR_MESSAGE_LIMIT],
        ),
    )
    db.execute(
        'UPDATE webhooks SET last_triggered_at = ? WHERE id = ?',
        (_now_iso(), result.webhook_id),
    )
    db.commit()


def list_recent_deliveries(
    db: sqlite3.Connection, *, webhook_id: int | None = None, limit: int = 50
) -> list[dict]:
    """Return the newest ``limit`` delivery rows, optionally filtered."""
    limit = max(1, min(int(limit), 500))
    if webhook_id is None:
        rows = db.execute(
            'SELECT * FROM webhook_deliveries ORDER BY created_at DESC, id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT * FROM webhook_deliveries WHERE webhook_id = ? '
            'ORDER BY created_at DESC, id DESC LIMIT ?',
            (webhook_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def purge_old_deliveries(db: sqlite3.Connection, *, keep_days: int = 30) -> int:
    """Delete delivery rows older than ``keep_days``. Returns the deleted count."""
    keep_days = max(1, int(keep_days))
    cursor = db.execute(
        'DELETE FROM webhook_deliveries '
        "WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (f'-{keep_days} days',),
    )
    db.commit()
    return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Auto-disable
# ---------------------------------------------------------------------------


def reset_failures(db: sqlite3.Connection, webhook_id: int) -> None:
    """Zero the consecutive-failure counter after a successful delivery."""
    db.execute('UPDATE webhooks SET failure_count = 0 WHERE id = ?', (webhook_id,))
    db.commit()


def increment_failures(db: sqlite3.Connection, webhook_id: int, *, threshold: int = 10) -> bool:
    """Bump ``failure_count`` and auto-disable when it crosses ``threshold``.

    Returns ``True`` when the row was just disabled by this call (so
    the caller can log a WARNING). Returns ``False`` otherwise.

    A non-positive ``threshold`` disables the auto-disable behaviour
    entirely — failures still accumulate but the row never flips off.
    """
    db.execute(
        'UPDATE webhooks SET failure_count = failure_count + 1 WHERE id = ?',
        (webhook_id,),
    )
    db.commit()

    if threshold <= 0:
        return False

    row = db.execute(
        'SELECT failure_count, enabled FROM webhooks WHERE id = ?',
        (webhook_id,),
    ).fetchone()
    if row is None:
        return False
    if row['enabled'] and row['failure_count'] >= threshold:
        db.execute('UPDATE webhooks SET enabled = 0 WHERE id = ?', (webhook_id,))
        db.commit()
        return True
    return False


# ---------------------------------------------------------------------------
# Signing + delivery
# ---------------------------------------------------------------------------


def sign_payload(secret: str | bytes, body: str | bytes) -> str:
    """Return the hex HMAC-SHA256 of ``body`` keyed by ``secret``.

    Both arguments accept ``bytes`` or ``str`` (strings are encoded as
    UTF-8) — keeps test fixtures and downstream verifiers from having
    to pre-encode.
    """
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    if isinstance(body, str):
        body = body.encode('utf-8')
    return hmac.new(secret, body, sha256).hexdigest()


def _build_envelope(event_name, payload):
    """Canonical wire envelope. Stable so downstream verifiers can rely on it.

    JSON serialisation uses ``sort_keys=True`` so the same payload
    always hashes to the same signature regardless of dict iteration
    order.
    """
    envelope = {
        'event': event_name,
        'timestamp': _now_iso(),
        'data': payload,
    }
    return json.dumps(envelope, sort_keys=True, default=str).encode('utf-8')


def deliver_now(
    webhook: Webhook, event_name: str, payload: dict, *, timeout: int = 5
) -> DeliveryResult:
    """Synchronously POST ``payload`` to ``webhook.url`` and return the result.

    Never raises — every failure mode is captured in the returned
    :class:`DeliveryResult` (status_code 0 for network errors / timeouts).
    """
    body = _build_envelope(event_name, payload)
    request = Request(  # noqa: S310  # nosec B310 — operator-supplied URL is the entire point of the feature
        webhook.url,
        data=body,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT,
            EVENT_HEADER: event_name,
            SIGNATURE_HEADER: sign_payload(webhook.secret, body),
        },
    )
    start = time.monotonic()
    try:
        # B310 / S310: urlopen is the entire point of the feature; the URL
        # comes from an admin-managed `webhooks` row (operator-supplied).
        # The Request constructor above is what bandit flags; the
        # suppression there + this one cover both rules.
        with urlopen(request, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = getattr(resp, 'status', None) or resp.getcode()
            return DeliveryResult(
                webhook_id=webhook.id,
                event=event_name,
                status_code=int(status),
                response_time_ms=elapsed_ms,
                error='',
            )
    except HTTPError as exc:
        # Server reachable but returned >=400. The status code is
        # meaningful — record it rather than collapsing to 0 like the
        # network-error branch.
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return DeliveryResult(
            webhook_id=webhook.id,
            event=event_name,
            status_code=int(exc.code),
            response_time_ms=elapsed_ms,
            error=f'HTTP {exc.code}: {exc.reason}',
        )
    except (URLError, TimeoutError, OSError) as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        reason = getattr(exc, 'reason', exc)
        return DeliveryResult(
            webhook_id=webhook.id,
            event=event_name,
            status_code=0,
            response_time_ms=elapsed_ms,
            error=f'{type(exc).__name__}: {reason}',
        )


# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------


def _deliver_and_record(db_path, webhook_id, event_name, payload, *, timeout, threshold):
    """Worker function: open a fresh DB connection, deliver, log, update.

    Runs on a daemon thread spawned by :func:`dispatch_event_async`.
    Catches every exception so a runaway bug here can't kill the
    Python interpreter on process shutdown (daemon threads still want
    to exit cleanly on KeyboardInterrupt).
    """
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys = ON')
            webhook = get_webhook(conn, webhook_id)
            if webhook is None or not webhook.enabled:
                return  # raced with admin delete / disable
            result = deliver_now(webhook, event_name, payload, timeout=timeout)
            record_delivery(conn, result)
            ok = 200 <= result.status_code < 300
            if ok:
                reset_failures(conn, webhook_id)
            else:
                disabled = increment_failures(conn, webhook_id, threshold=threshold)
                if disabled:
                    _log.warning(
                        'webhook auto-disabled after %d consecutive failures: '
                        'id=%d name=%s url=%s last_status=%d',
                        threshold,
                        webhook.id,
                        webhook.name,
                        webhook.url,
                        result.status_code,
                    )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail-open per module docstring
        _log.warning(
            'webhook delivery worker raised: webhook_id=%s event=%s exc=%r',
            webhook_id,
            event_name,
            exc,
        )


def dispatch_event_async(
    db_path: str,
    event_name: str,
    payload: dict,
    *,
    timeout: int = 5,
    threshold: int = 10,
    _join_for_tests: bool = False,
) -> list[threading.Thread]:
    """Find matching enabled webhooks and spawn one daemon thread per delivery.

    ``_join_for_tests`` is an undocumented kwarg used by the test suite
    to wait for every spawned thread to finish before assertions run.
    Production callers must NOT pass it — it negates the async-ness.

    Returns the list of started ``threading.Thread`` objects so test
    helpers can ``join()`` them; production callers should ignore the
    return value.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            subscribers = list_enabled_subscribers(conn, event_name)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail-open
        _log.warning('webhook subscriber lookup failed: event=%s exc=%r', event_name, exc)
        return []

    threads = []
    for webhook in subscribers:
        thread = threading.Thread(
            target=_deliver_and_record,
            args=(db_path, webhook.id, event_name, payload),
            kwargs={'timeout': timeout, 'threshold': threshold},
            daemon=True,
            name=f'webhook-{webhook.id}-{event_name}',
        )
        thread.start()
        threads.append(thread)

    if _join_for_tests:
        for thread in threads:
            thread.join(timeout=10)

    return threads


# ---------------------------------------------------------------------------
# Bus integration
# ---------------------------------------------------------------------------


def _settings_snapshot(db_path):
    """Read the three webhook-related settings without touching Flask.

    Returns ``(enabled: bool, timeout: int, threshold: int)``. Falls
    back to safe defaults on any error so the bus handler never raises
    inside the dispatcher.
    """
    enabled = False
    timeout = 5
    threshold = 10
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = conn.execute(
                'SELECT key, value FROM settings WHERE key IN '
                "('webhooks_enabled', 'webhook_timeout_seconds', 'webhook_failure_threshold')"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — settings unreachable → behave as disabled
        return False, timeout, threshold

    for key, value in rows:
        if key == 'webhooks_enabled':
            enabled = str(value).strip().lower() in {'1', 'true', 'yes', 'on'}
        elif key == 'webhook_timeout_seconds':
            with contextlib.suppress(TypeError, ValueError):
                timeout = max(1, min(int(value), 60))
        elif key == 'webhook_failure_threshold':
            with contextlib.suppress(TypeError, ValueError):
                threshold = max(0, int(value))
    return enabled, timeout, threshold


def register_bus_handlers(db_path: str) -> None:
    """Subscribe one handler per :class:`Events` constant.

    Called once at app startup from ``app.__init__.create_app``. The
    handler closure captures ``db_path`` so the dispatcher works
    without a Flask application context (the bus is sometimes invoked
    from CLI / cron paths that don't have one).

    Each handler:

    1. Reads the three webhook-related settings from a fresh sqlite3
       connection. Cheap (three rows, one query).
    2. Short-circuits if the master toggle is off.
    3. Otherwise calls :func:`dispatch_event_async` to fan out.

    **Idempotent.** Re-registering against the same ``db_path`` first
    unregisters the previous handlers — keeps the test suite from
    accumulating duplicates when the bus's autouse ``clear()`` fixture
    runs out of order with the ``app`` fixture (which auto-registers
    via ``create_app``). Production never calls this twice in the same
    process.
    """
    from app.events import Events, register, unregister

    # Drop any handlers previously registered for this db_path. Walks
    # the bus's private storage via the `__webhooks_db_path__` tag we
    # stamp on every closure below.
    from app.events import _handlers as _bus_handlers  # noqa: PLC2701 — module-internal helper

    for name, handlers in list(_bus_handlers.items()):
        for handler in list(handlers):
            if getattr(handler, '__webhooks_db_path__', None) == db_path:
                unregister(name, handler)

    def _make_handler(event_name):
        def _handler(**payload):
            enabled, timeout, threshold = _settings_snapshot(db_path)
            if not enabled:
                return
            dispatch_event_async(
                db_path,
                event_name,
                payload,
                timeout=timeout,
                threshold=threshold,
            )

        # Tags so re-registration can find and remove our previous closures.
        _handler.__webhooks_event__ = event_name  # type: ignore[attr-defined]
        _handler.__webhooks_db_path__ = db_path  # type: ignore[attr-defined]
        return _handler

    for name in Events.ALL:
        register(name, _make_handler(name))
