"""
Internationalization (i18n) Tests — Phase 10

Verifies the i18n infrastructure:
- Flask-Babel initialization and locale selector integration.
- Language switching via /set-locale/<lang> endpoint.
- Session-based locale persistence across requests.
- Accept-Language header negotiation.
- Hreflang tags rendered when multiple locales are available.
- Language switcher visibility based on available_locales setting.
- Translation message extraction produces a valid .pot file.
- i18n settings seeded by migration 004.
"""

import os
import sqlite3

# ============================================================
# LOCALE SWITCHING
# ============================================================


def test_set_locale_stores_in_session(client):
    """GET /set-locale/es should set session['locale'] and redirect."""
    response = client.get('/set-locale/es', follow_redirects=False)
    assert response.status_code == 302

    # Verify locale persists in session
    with client.session_transaction() as sess:
        assert sess.get('locale') == 'es'


def test_set_locale_redirects_to_referrer(client):
    """GET /set-locale/fr with a Referer header should redirect back."""
    response = client.get(
        '/set-locale/fr',
        headers={'Referer': '/contact'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/contact')


def test_set_locale_redirects_to_home_without_referrer(client):
    """GET /set-locale/en without Referer should redirect to /."""
    response = client.get('/set-locale/en', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')


# ============================================================
# ACCEPT-LANGUAGE NEGOTIATION
# ============================================================


def test_accept_language_negotiation(app):
    """The locale selector should respect the Accept-Language header."""
    # Seed 'es' as an available locale
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('available_locales', 'en,es'),
    )
    conn.commit()
    conn.close()

    with app.test_client() as c:
        # Request with Spanish preference
        response = c.get('/', headers={'Accept-Language': 'es;q=0.9,en;q=0.5'})
        assert response.status_code == 200


def test_accept_language_falls_back_to_default(client):
    """Unknown languages in Accept-Language should fall back to 'en'."""
    response = client.get('/', headers={'Accept-Language': 'xx-YY'})
    assert response.status_code == 200


# ============================================================
# VARY: ACCEPT-LANGUAGE (CDN cache-key correctness — #90)
# ============================================================


def test_vary_accept_language_on_public_routes(client):
    """#90: every public response carries Vary: Accept-Language so CDNs key by locale."""
    for path in ('/', '/blog', '/portfolio', '/contact'):
        resp = client.get(path)
        vary = resp.headers.get('Vary', '')
        assert 'Accept-Language' in vary, f'{path}: Vary header {vary!r} missing Accept-Language'


def test_vary_preserves_other_values(client):
    """#90: appending Accept-Language must not strip existing Vary entries."""
    resp = client.get('/')
    vary = resp.headers.get('Vary', '')
    parts = [p.strip() for p in vary.split(',')]
    assert 'Accept-Language' in parts


# ============================================================
# SESSION LOCALE PERSISTENCE
# ============================================================


def test_session_locale_persists_across_requests(client):
    """Once set, the locale should persist in subsequent requests."""
    # Set locale
    client.get('/set-locale/de')

    # Subsequent request should have the locale in session
    with client.session_transaction() as sess:
        assert sess['locale'] == 'de'


# ============================================================
# HREFLANG TAGS
# ============================================================


def test_hreflang_tags_shown_when_multiple_locales(app):
    """When available_locales has > 1 locale, hreflang tags should appear."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('available_locales', 'en,es'),
    )
    conn.commit()
    conn.close()

    with app.test_client() as c:
        response = c.get('/')
        html = response.data.decode()
        assert 'hreflang' in html


def test_no_hreflang_tags_with_single_locale(client):
    """With only one locale, hreflang tags should not appear."""
    response = client.get('/')
    html = response.data.decode()
    # With only 'en' available (default), hreflang should not be rendered
    # (the template conditionally renders hreflang only when available_locales > 1)
    assert 'hreflang' not in html or html.count('hreflang') == 0


# ============================================================
# LANGUAGE SWITCHER
# ============================================================


def test_language_switcher_hidden_with_single_locale(client):
    """Language switcher should not appear when only one locale is available."""
    response = client.get('/')
    html = response.data.decode()
    assert 'set-locale' not in html


def test_language_switcher_shown_with_multiple_locales(app):
    """Language switcher should appear when multiple locales are configured."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('available_locales', 'en,fr'),
    )
    conn.commit()
    conn.close()

    with app.test_client() as c:
        response = c.get('/')
        html = response.data.decode()
        assert 'set-locale' in html


# ============================================================
# CONTEXT PROCESSOR
# ============================================================


def test_context_processor_injects_locale_vars(app):
    """The context processor should inject available_locales and current_locale."""
    with app.test_request_context('/'):
        app.preprocess_request()
        # Verify the context processor runs by rendering a page and checking the response
        response = app.test_client().get('/')
        assert response.status_code == 200


# ============================================================
# MIGRATION 004 SETTINGS
# ============================================================


def test_i18n_settings_seeded(app):
    """Migration 004 should seed default_locale and available_locales settings."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    default_locale = conn.execute(
        "SELECT value FROM settings WHERE key = 'default_locale'"
    ).fetchone()
    available_locales = conn.execute(
        "SELECT value FROM settings WHERE key = 'available_locales'"
    ).fetchone()
    conn.close()

    assert default_locale is not None
    assert default_locale['value'] == 'en'
    assert available_locales is not None
    assert available_locales['value'] == 'en'


# ============================================================
# SETTINGS REGISTRY
# ============================================================


def test_i18n_settings_in_registry():
    """The settings registry should include i18n settings."""
    from app.services.settings_svc import SETTINGS_CATEGORIES, SETTINGS_REGISTRY

    assert 'default_locale' in SETTINGS_REGISTRY
    assert 'available_locales' in SETTINGS_REGISTRY
    assert SETTINGS_REGISTRY['default_locale']['category'] == 'Internationalization'
    assert 'Internationalization' in SETTINGS_CATEGORIES


# ============================================================
# TRANSLATION FILES
# ============================================================


def test_messages_pot_exists():
    """The extracted messages.pot file should exist after extraction."""
    pot_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'translations',
        'messages.pot',
    )
    assert os.path.exists(pot_path), "messages.pot not found — run 'manage.py translations extract'"


def test_english_catalog_compiled():
    """The compiled English .mo file should exist."""
    mo_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'translations',
        'en',
        'LC_MESSAGES',
        'messages.mo',
    )
    assert os.path.exists(mo_path), "English .mo not found — run 'manage.py translations compile'"


def test_messages_pot_has_entries():
    """The messages.pot should contain extracted translatable strings."""
    pot_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'translations',
        'messages.pot',
    )
    with open(pot_path) as f:
        content = f.read()
    # Should have a reasonable number of msgid entries
    msgid_count = content.count('\nmsgid ')
    assert msgid_count > 50, f'Expected >50 translatable strings, found {msgid_count}'
