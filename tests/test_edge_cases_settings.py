"""
Edge-case tests for the settings service + admin bulk-save — Phase 18.13.

The settings surface has two entry points:
    * ``POST /admin/settings`` (HTML form, Flask-Login session auth)
    * ``PUT /api/v1/admin/settings`` (JSON body, admin-scoped API token)

Both route through ``save_many`` / the API handler's cleaned dict, which:
    1. Filter to keys in ``SETTINGS_REGISTRY`` (unknown keys are silently
       dropped).
    2. Coerce boolean-typed settings to the string literals 'true' / 'false'.
    3. Stringify everything else — there is no length limit, per-key
       validation, or type enforcement beyond the boolean case.

These tests pin down the current contract so regressions (e.g. a new
feature introducing implicit max length) are caught. They also verify the
cache invalidation path so admin-edit-then-read round-trips return the new
value within the same request cycle.
"""

from __future__ import annotations

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
def api_admin_token(app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='settings-edge', scope='admin').raw


def _auth(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _read_raw_value(app, key):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Unknown / out-of-registry keys
# ---------------------------------------------------------------------------


def test_api_unknown_key_is_silently_dropped(client, no_rate_limits, api_admin_token, app):
    response = client.put(
        '/api/v1/admin/settings',
        json={'bogus_key_1': 'x', 'hacker_key': 'y'},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body['data']['updated_keys'] == []
    # And nothing was actually written to the table
    for bad in ('bogus_key_1', 'hacker_key'):
        assert _read_raw_value(app, bad) is None


def test_api_mix_of_valid_and_invalid_keys_only_writes_valid(
    client, no_rate_limits, api_admin_token, app
):
    response = client.put(
        '/api/v1/admin/settings',
        json={
            'site_title': 'valid',
            'malicious_injection': '<script>alert(1)</script>',
        },
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert response.get_json()['data']['updated_keys'] == ['site_title']
    assert _read_raw_value(app, 'site_title') == 'valid'
    assert _read_raw_value(app, 'malicious_injection') is None


def test_form_unknown_key_silently_dropped(auth_client, app):
    """HTML form path: extra fields outside the registry are ignored."""
    response = auth_client.post(
        '/admin/settings',
        data={
            'site_title': 'Form update',
            'malicious': 'x',
            'csrf_token': 'dummy',  # CSRF disabled in tests
        },
        follow_redirects=False,
    )
    assert response.status_code in (200, 302)
    assert _read_raw_value(app, 'site_title') == 'Form update'
    assert _read_raw_value(app, 'malicious') is None


# ---------------------------------------------------------------------------
# Boolean coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('truthy', [True, 'true', 'True', 1, '1'])
def test_api_boolean_truthy_coerces_to_true_literal(
    client, no_rate_limits, api_admin_token, app, truthy
):
    response = client.put(
        '/api/v1/admin/settings',
        json={'dark_mode_default': truthy},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'dark_mode_default') == 'true'


@pytest.mark.parametrize('falsy', [False, 'false', 0, 'no', 'anything-else'])
def test_api_boolean_falsy_coerces_to_false_literal(
    client, no_rate_limits, api_admin_token, app, falsy
):
    response = client.put(
        '/api/v1/admin/settings',
        json={'dark_mode_default': falsy},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'dark_mode_default') == 'false'


def test_api_null_boolean_becomes_false(client, no_rate_limits, api_admin_token, app):
    response = client.put(
        '/api/v1/admin/settings',
        json={'dark_mode_default': None},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'dark_mode_default') == 'false'


# ---------------------------------------------------------------------------
# Type coercion for non-boolean fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'value,expected_stored',
    [
        (123, '123'),  # int → str
        (12.5, '12.5'),  # float → str
        (True, 'True'),  # bool → str (non-bool key: no literal coercion)
        (None, ''),  # None → empty string
        ([], '[]'),  # list → str(list) — not rejected, just stringified
        ({'nested': 1}, "{'nested': 1}"),  # dict → repr
    ],
)
def test_non_bool_values_are_stringified(
    client, no_rate_limits, api_admin_token, app, value, expected_stored
):
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': value},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'site_title') == expected_stored


# ---------------------------------------------------------------------------
# Oversized values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('size', [1, 100, 1_000, 10_000, 100_000])
def test_values_of_various_lengths_are_accepted(client, no_rate_limits, api_admin_token, app, size):
    """SQLite TEXT has no length enforcement; the handler does not impose
    a cap. This test pins down the current contract — if we ever add a
    per-key cap, these assertions become the canary.
    """
    payload = 'x' * size
    response = client.put(
        '/api/v1/admin/settings',
        json={'footer_text': payload},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'footer_text') == payload


def test_huge_payload_does_not_500(client, no_rate_limits, api_admin_token):
    """A 1 MB footer must not crash the serialiser or the DB insert."""
    huge = 'a' * (1 << 20)
    response = client.put(
        '/api/v1/admin/settings',
        json={'footer_text': huge},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Unicode values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'title',
    [
        'Simple ASCII',
        'Café au lait',
        '漢字の設定',
        '🏠 Portfolio 🎨',
        'עברית',  # RTL Hebrew
        'مرحبا',  # RTL Arabic
        'e\u0301 combining',
        '\u200d zero-width-joiner\u200d',
    ],
)
def test_unicode_values_round_trip(client, no_rate_limits, api_admin_token, app, title):
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': title},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _read_raw_value(app, 'site_title') == title


# ---------------------------------------------------------------------------
# Injection payloads — settings values are stored verbatim; rendering
# is the layer that must escape them. Here we only check the write path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'payload',
    [
        "'; DROP TABLE settings;--",
        '<script>alert(1)</script>',
        '{{ 7*7 }}',
        '../../../etc/passwd',
        '\x00null-byte',
    ],
)
def test_injection_payloads_stored_as_strings(
    client, no_rate_limits, api_admin_token, app, payload
):
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': payload},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200
    # Settings table still intact (a DROP would have taken it out)
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        count = conn.execute('SELECT COUNT(*) FROM settings').fetchone()[0]
    finally:
        conn.close()
    assert count > 0


# ---------------------------------------------------------------------------
# Cache invalidation — bulk-save must make the new value visible to the
# same connection immediately (this is the regression guard for the TTL
# cache introduced in Phase 12.1).
# ---------------------------------------------------------------------------


def test_bulk_save_invalidates_cache(client, no_rate_limits, api_admin_token, app):
    from app.services.settings_svc import get_all_cached

    # Prime the cache with the current value
    with app.app_context():
        from app.db import get_db

        db = get_db()
        first = get_all_cached(db, app.config['DATABASE_PATH'])
    old_title = first.get('site_title', '')

    # Write via the API
    new_title = f'{old_title}-updated'
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': new_title},
        headers=_auth(api_admin_token),
    )
    assert response.status_code == 200

    # Next read (even within TTL) must see the new value — invalidate_cache
    # ran inside save_many / the admin-settings handler.
    with app.app_context():
        from app.db import get_db as _get_db

        second = get_all_cached(_get_db(), app.config['DATABASE_PATH'])
    assert second['site_title'] == new_title


# ---------------------------------------------------------------------------
# Service-level unit tests for save_many's checkbox semantics
# ---------------------------------------------------------------------------


def test_save_many_treats_absent_bool_as_false(app):
    """Form-submit path: a boolean key absent from ``form_data`` means
    the checkbox was unchecked → store 'false'. This mirrors HTML form
    semantics and is the reason the HTML handler must POST every key.
    """
    from app.services.settings_svc import save_many

    with app.app_context():
        from app.db import get_db

        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('dark_mode_default', 'true')"
        )
        db.commit()

        save_many(db, {'site_title': 'abc'})  # no dark_mode_default key

    # The admin form semantic flipped it to 'false'
    assert _read_raw_value(app, 'dark_mode_default') == 'false'


def test_save_many_accepts_present_bool_true(app):
    from app.services.settings_svc import save_many

    with app.app_context():
        from app.db import get_db

        db = get_db()
        save_many(db, {'dark_mode_default': 'true'})

    assert _read_raw_value(app, 'dark_mode_default') == 'true'


def test_set_one_rejects_unknown_key(app):
    from app.exceptions import NotFoundError
    from app.services.settings_svc import set_one

    with app.app_context():
        from app.db import get_db

        db = get_db()
        with pytest.raises(NotFoundError):
            set_one(db, 'totally-made-up', 'x')


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_writes_never_500(app, no_rate_limits, api_admin_token):
    """Two admin clients saving the same key simultaneously should race but
    never 500. The last write wins (SQLite serialisation guarantees a
    coherent final state).
    """
    errors: list[BaseException] = []
    status_codes: list[int] = []
    lock = threading.Lock()

    def write(i):
        try:
            with app.test_client() as c:
                r = c.put(
                    '/api/v1/admin/settings',
                    json={'site_title': f'writer-{i}'},
                    headers=_auth(api_admin_token),
                )
                with lock:
                    status_codes.append(r.status_code)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert all(code == 200 for code in status_codes)
    # Final stored value must be one of the writers' values — not a merge
    # of two (SQLite PRIMARY KEY conflict-resolution makes that impossible,
    # but we assert here to catch any future regression to a merging write).
    final = _read_raw_value(app, 'site_title')
    assert final.startswith('writer-')
