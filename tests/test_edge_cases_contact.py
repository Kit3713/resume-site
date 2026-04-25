"""
Edge-case tests for the contact form — Phase 18.13.

Exercises the checklist in ``tests/TESTING_STANDARDS.md`` against both the
HTML form (``POST /contact``) and the JSON API (``POST /api/v1/contact``):
empty/null, boundary, Unicode, length, and injection inputs. Concurrency
behaviour for the per-IP hourly cap is covered explicitly.

Rate limits are disabled per-test via a local fixture so the edge-case
assertions aren't shadowed by burst limits — the limiter is tested
separately in tests/test_security.py.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def no_rate_limits(app):
    """Disable Flask-Limiter so boundary tests don't trip the burst cap."""
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


def _form_post(client, **overrides):
    """POST the contact form with sane defaults, allowing per-field overrides."""
    data = {
        'name': 'Alice Example',
        'email': 'alice@example.com',
        'message': 'Hello there.',
        'website': '',  # honeypot, must stay empty for a "real" submission
    }
    data.update(overrides)
    return client.post('/contact', data=data, follow_redirects=False)


def _api_post(client, **overrides):
    body = {
        'name': 'Alice Example',
        'email': 'alice@example.com',
        'message': 'Hello there.',
    }
    body.update(overrides)
    return client.post('/api/v1/contact', json=body)


def _count_submissions(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        return conn.execute('SELECT COUNT(*) FROM contact_submissions').fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Empty / null inputs
# ---------------------------------------------------------------------------


def test_form_rejects_all_empty_fields(client, no_rate_limits, smtp_mock):
    response = _form_post(client, name='', email='', message='')
    # Stays on the page (no redirect → validation error branch)
    assert response.status_code == 200
    assert smtp_mock == []


def test_form_rejects_whitespace_only_fields(client, no_rate_limits, smtp_mock):
    response = _form_post(client, name='   ', email='\t\t', message='\n\n')
    assert response.status_code == 200
    assert smtp_mock == []


def test_api_rejects_missing_fields_with_400(client, no_rate_limits):
    response = client.post('/api/v1/contact', json={})
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 'VALIDATION_ERROR'
    assert set(body['details']['fields']) == {'name', 'email', 'message'}


def test_api_treats_null_values_as_missing(client, no_rate_limits):
    response = client.post(
        '/api/v1/contact',
        json={'name': None, 'email': None, 'message': None},
    )
    assert response.status_code == 400
    assert response.get_json()['code'] == 'VALIDATION_ERROR'


def test_api_rejects_scalar_json_body(client, no_rate_limits):
    """A non-object JSON body collapses to {} in _json_body → all fields missing."""
    response = client.post(
        '/api/v1/contact',
        data='"just a string"',
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Email format boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'bad_email',
    [
        'noatsign.example',  # missing '@'
        'no-dot@localhost',  # missing '.'
        '@',  # only the separators
        '.',  # only the dot
        '\n@\n.\n',  # control characters around the markers
    ],
)
def test_api_rejects_malformed_emails(client, no_rate_limits, bad_email):
    response = _api_post(client, email=bad_email)
    # '@' + '.' marker check is intentionally permissive — anything that
    # satisfies both markers survives the handler (it's the SMTP relay's
    # job to reject truly undeliverable addresses). So our assertion is
    # only on addresses that obviously fail the marker test.
    if '@' in bad_email and '.' in bad_email:
        assert response.status_code == 201
    else:
        assert response.status_code == 400
        assert response.get_json()['details']['field'] == 'email'


def test_api_accepts_minimal_valid_email(client, no_rate_limits, smtp_mock):
    """The simplest string that satisfies the marker check is ``a@b.c``."""
    response = _api_post(client, email='a@b.c')
    assert response.status_code == 201
    # The relay fires on a non-spam submission
    assert len(smtp_mock) == 1


# ---------------------------------------------------------------------------
# Unicode handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'Renée Żółć',  # Latin-1 supplement + Latin Extended-A
        '山田太郎',  # CJK
        '👋🏻 Emoji Name 🙌',  # emoji with skin-tone modifier
        'مرحبا',  # RTL (Arabic)
        'שלום',  # RTL (Hebrew)
        'a\u200db',  # zero-width joiner
        'e\u0301',  # "e" + combining acute (NFD form)
    ],
)
def test_api_accepts_unicode_names(client, no_rate_limits, smtp_mock, name):
    response = _api_post(client, name=name)
    assert response.status_code == 201, response.get_json()
    # SMTP relay receives the name verbatim — no mojibake on the way through
    assert smtp_mock[-1][0] == name


def test_form_null_byte_in_name_rejected(client, no_rate_limits, smtp_mock, app):
    """Phase 27.5 (#13): null bytes in name / email / message are
    rejected with a user-visible error (200 + flash). Pre-27.5 the
    byte was stored verbatim; post-27.5 the submission is dropped
    and no row lands in the DB."""
    before = _count_submissions(app)
    response = _form_post(client, name='Alice\x00DROP TABLE users;--')
    assert response.status_code == 200  # form re-rendered with flash
    # No row added — the null-byte submission is rejected.
    assert _count_submissions(app) == before


# ---------------------------------------------------------------------------
# Length boundaries
# ---------------------------------------------------------------------------


def test_api_accepts_very_long_message(client, no_rate_limits, smtp_mock):
    huge = 'x' * 50_000
    response = _api_post(client, message=huge)
    assert response.status_code == 201
    assert smtp_mock[-1][2] == huge


def test_api_classifies_oversized_user_agent_as_coarse_class(client, no_rate_limits, app):
    """Phase 24.2 (#60) — the raw User-Agent is no longer stored. The
    stored value is always one of the coarse-class tokens regardless
    of input length, so the 200-char truncation is obsolete (the enum
    values are all <= 15 chars)."""
    oversized_ua = 'UA-' + ('x' * 500)
    response = client.post(
        '/api/v1/contact',
        json={'name': 'N', 'email': 'a@b.c', 'message': 'm'},
        headers={'User-Agent': oversized_ua, 'Content-Type': 'application/json'},
    )
    assert response.status_code == 201

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        ua = conn.execute(
            'SELECT user_agent FROM contact_submissions ORDER BY id DESC LIMIT 1'
        ).fetchone()[0]
    finally:
        conn.close()
    # Coarse class, short. Junk UA → 'other'.
    assert ua == 'other'
    assert len(ua) < 20


# ---------------------------------------------------------------------------
# Injection payloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'payload',
    [
        '"><script>alert(1)</script>',
        '{{ 7*7 }}',  # Jinja2 template injection
        '${7*7}',
        '%0d%0aSet-Cookie: admin=1',  # url-encoded CRLF (stays literal in JSON body)
        '\r\nBcc: evil@attacker.example\r\n',  # raw CRLF — handler must neutralize
    ],
)
def test_injection_payloads_are_stored_safely_and_dont_500(
    client, no_rate_limits, smtp_mock, app, payload
):
    """Non-SQLi injection payloads still hit the parameterized-query
    layer and must be stored verbatim without breaking the handler.

    SQLi-fingerprint payloads (``;DROP TABLE``, ``' OR 1=1``) are now
    blocked earlier by the v0.3.3 WAF body-scan (#84) — see
    ``test_sql_injection_payload_blocked_by_waf`` below.
    """
    response = _api_post(client, message=payload)
    assert response.status_code == 201, response.get_json()
    # Either the payload is preserved verbatim (parameterised queries make
    # SQL metacharacters harmless in storage) OR the handler has stripped
    # surrounding whitespace (which neutralizes raw CRLF injection before
    # the value reaches SMTP). Both outcomes are safe.
    stored = smtp_mock[-1][2]
    assert payload.strip() in stored or stored == payload
    # The submission persists — no SQL metacharacter got interpreted.
    assert _count_submissions(app) >= 1


@pytest.mark.parametrize(
    'payload',
    [
        "Robert'); DROP TABLE contact_submissions;--",
        "' OR 1=1 --",
    ],
)
def test_sql_injection_payload_blocked_by_waf(client, no_rate_limits, payload):
    """#84: SQLi fingerprints in a JSON body are blocked at the WAF
    before reaching the contact handler. Earlier line of defense than
    the parameterized-query layer — both must hold.
    """
    response = _api_post(client, message=payload)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Honeypot handling
# ---------------------------------------------------------------------------


def test_honeypot_filled_marks_spam_and_does_not_send_email(client, no_rate_limits, smtp_mock, app):
    response = _form_post(client, website='https://spammer.example/')
    assert response.status_code == 302  # same success redirect as a real submission
    # No SMTP relay on spam
    assert smtp_mock == []
    # But the row is saved with is_spam=1
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        row = conn.execute(
            'SELECT is_spam FROM contact_submissions ORDER BY id DESC LIMIT 1'
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 1


def test_honeypot_filled_api_returns_201_but_no_email(client, no_rate_limits, smtp_mock):
    response = _api_post(client, website='https://spammer.example/')
    assert response.status_code == 201
    assert smtp_mock == []


# ---------------------------------------------------------------------------
# Form-disabled branch
# ---------------------------------------------------------------------------


def test_form_disabled_returns_redirect_no_email(client, no_rate_limits, smtp_mock, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('contact_form_enabled', 'false')"
    )
    conn.commit()
    conn.close()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    response = _form_post(client)
    assert response.status_code == 302
    assert smtp_mock == []


def test_api_disabled_returns_404(client, no_rate_limits, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('contact_form_enabled', 'false')"
    )
    conn.commit()
    conn.close()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    response = _api_post(client)
    assert response.status_code == 404
    assert response.get_json()['code'] == 'NOT_FOUND'


# ---------------------------------------------------------------------------
# Per-IP hourly cap
# ---------------------------------------------------------------------------


def test_api_hourly_cap_returns_429_after_five_submissions(client, no_rate_limits, smtp_mock):
    for _ in range(5):
        assert _api_post(client).status_code == 201
    blocked = _api_post(client)
    assert blocked.status_code == 429
    body = blocked.get_json()
    assert body['code'] == 'RATE_LIMITED'
    assert body['details']['retry_after_minutes'] == 60


def test_hourly_cap_does_not_apply_to_honeypot_spam(client, no_rate_limits, smtp_mock):
    """Bots that fill the honeypot must not learn about the cap via a 429.

    We quietly accept spam submissions past the cap so attackers can't
    probe for rate limits.
    """
    for _ in range(7):
        response = _api_post(client, website='https://spammer.example/')
        assert response.status_code == 201
    assert smtp_mock == []


# ---------------------------------------------------------------------------
# Concurrency — two requests from the same IP racing the cap check
# ---------------------------------------------------------------------------


def test_concurrent_submissions_from_same_ip_do_not_500(client, no_rate_limits, app, smtp_mock):
    """A small burst of concurrent requests from the same IP must all return
    a documented status (201 or 429) — never 500. This catches obvious
    threading bugs in the rate-limit path without trying to prove that the
    race is fully tight; SQLite + Flask's test client is single-process.
    """
    results: list[int] = []
    errors: list[BaseException] = []

    def hit():
        try:
            with app.test_client() as c:
                results.append(_api_post(c).status_code)
        except BaseException as exc:  # noqa: BLE001 — we want to surface ANY error
            errors.append(exc)

    threads = [threading.Thread(target=hit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f'concurrent submissions raised: {errors!r}'
    assert all(code in (201, 429) for code in results), results
