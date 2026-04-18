"""
Phase 16.1 (Accept-Language) — API content-negotiation tests

Verifies the API read endpoints that were unblocked by Phase 15.4:

    * `Accept-Language` is parsed via Werkzeug's `best_match` against
      the configured `available_locales`.
    * Translated fields overlay the default-locale values when a
      matching translation row exists.
    * Untranslated fields fall back to the default locale so the payload
      is always complete.
    * Response carries `Content-Language` (the locale actually served)
      and `Vary: Accept-Language` (so caches key correctly).
    * The ETag varies across locales (sanity check that the 304 path
      doesn't accidentally pin cross-locale).

Endpoints covered:
    GET /api/v1/content/{slug}, /services, /stats, /certifications,
    /projects, /projects/{slug}, /blog, /blog/{slug}.

The translation service unit tests and the HTML-side integration
tests (``tests/test_translations_public.py``) already cover the
overlay plumbing; this file focuses on the API's
content-negotiation + response-header contract.
"""

from __future__ import annotations

import json
import sqlite3

import pytest


def _json(response):
    """Return the parsed JSON body."""
    assert response.headers['Content-Type'].startswith('application/json')
    return json.loads(response.data.decode('utf-8'))


@pytest.fixture
def multilocale_app(app):
    """Configure `available_locales = en,es` for the app fixture.

    Returns a (app, db_conn) pair so tests can seed translations via
    the open connection without building a fresh one per row.
    """
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)',
        ('available_locales', 'en,es'),
    )
    conn.execute(
        'INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)',
        ('default_locale', 'en'),
    )
    conn.commit()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()
    yield app, conn
    conn.close()


def _save_translation(conn, table, parent_id, locale, **fields):
    from app.services.translations import save_translation

    save_translation(conn, table, parent_id, locale, **fields)
    conn.commit()


# ---------------------------------------------------------------------------
# /api/v1/services — the simplest Accept-Language integration surface
# ---------------------------------------------------------------------------


def test_services_falls_back_to_default_without_header(multilocale_app):
    """Missing Accept-Language returns the default locale row verbatim."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()

    response = app.test_client().get('/api/v1/services')
    assert response.status_code == 200
    body = _json(response)
    assert body['data'][0]['title'] == 'Consulting'
    assert response.headers['Content-Language'] == 'en'
    # Flask merges any existing Vary (e.g. Cookie from session handling)
    # with ours. Assert presence, not exact equality.
    assert 'Accept-Language' in response.headers.get('Vary', '')


def test_services_honours_accept_language_exact_match(multilocale_app):
    """`Accept-Language: es` surfaces the Spanish translation."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()
    service_id = conn.execute('SELECT id FROM services').fetchone()['id']
    _save_translation(
        conn, 'services', service_id, 'es', title='Consultoría', description='Descripción ES'
    )

    response = app.test_client().get('/api/v1/services', headers={'Accept-Language': 'es'})
    assert response.status_code == 200
    body = _json(response)
    assert body['data'][0]['title'] == 'Consultoría'
    assert body['data'][0]['description'] == 'Descripción ES'
    assert response.headers['Content-Language'] == 'es'


def test_services_q_value_preference(multilocale_app):
    """q-value negotiation picks the highest-weight configured locale."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()
    service_id = conn.execute('SELECT id FROM services').fetchone()['id']
    _save_translation(conn, 'services', service_id, 'es', title='Consultoría')

    # Client prefers Spanish over English; server picks es.
    response = app.test_client().get(
        '/api/v1/services', headers={'Accept-Language': 'es;q=0.9,en;q=0.5'}
    )
    assert response.headers['Content-Language'] == 'es'
    assert _json(response)['data'][0]['title'] == 'Consultoría'


def test_services_unconfigured_locale_falls_back_to_default(multilocale_app):
    """Requesting ``de`` when only en/es are configured returns English."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()

    response = app.test_client().get('/api/v1/services', headers={'Accept-Language': 'de'})
    assert response.status_code == 200
    assert response.headers['Content-Language'] == 'en'
    assert _json(response)['data'][0]['title'] == 'Consulting'


def test_services_missing_translation_row_uses_default_fields(multilocale_app):
    """When ``es`` is available but no translation row exists, serve English."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()

    response = app.test_client().get('/api/v1/services', headers={'Accept-Language': 'es'})
    assert response.status_code == 200
    assert response.headers['Content-Language'] == 'es'  # honoured the request
    # But content falls back because no translation row exists
    assert _json(response)['data'][0]['title'] == 'Consulting'


def test_services_etag_differs_per_locale(multilocale_app):
    """The ETag must vary across locales so 304 doesn't cross over.

    Regression guard: if a future refactor decouples the ETag from the
    serialized body (e.g., caches by URL only), an English client could
    receive a cached Spanish response on 304. This test locks in the
    current behaviour — the ETag is derived from the body bytes so
    cross-locale 304 is impossible.
    """
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Consulting', 'English desc', '', 1, 1)"
    )
    conn.commit()
    service_id = conn.execute('SELECT id FROM services').fetchone()['id']
    _save_translation(conn, 'services', service_id, 'es', title='Consultoría')

    c = app.test_client()
    en_etag = c.get('/api/v1/services').headers['ETag']
    es_etag = c.get('/api/v1/services', headers={'Accept-Language': 'es'}).headers['ETag']
    assert en_etag != es_etag


# ---------------------------------------------------------------------------
# /api/v1/content/<slug>
# ---------------------------------------------------------------------------


def test_content_block_locale_overlay(multilocale_app):
    """Content blocks respect Accept-Language on the single-item endpoint."""
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO content_blocks (slug, title, content, plain_text) '
        "VALUES ('about', 'About', '<p>English body</p>', 'English body')"
    )
    conn.commit()
    block_id = conn.execute("SELECT id FROM content_blocks WHERE slug='about'").fetchone()['id']
    _save_translation(
        conn,
        'content_blocks',
        block_id,
        'es',
        title='Sobre mí',
        content='<p>Cuerpo en español</p>',
    )

    response = app.test_client().get('/api/v1/content/about', headers={'Accept-Language': 'es'})
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['title'] == 'Sobre mí'
    assert body['data']['content'] == '<p>Cuerpo en español</p>'
    assert response.headers['Content-Language'] == 'es'


def test_content_block_404_respects_locale_headers(multilocale_app):
    """404 responses do NOT carry Content-Language — they're not translated."""
    app, _ = multilocale_app
    response = app.test_client().get(
        '/api/v1/content/nonexistent', headers={'Accept-Language': 'es'}
    )
    assert response.status_code == 404
    assert 'Content-Language' not in response.headers


# ---------------------------------------------------------------------------
# /api/v1/stats
# ---------------------------------------------------------------------------


def test_stats_locale_overlay(multilocale_app):
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO stats (label, value, suffix, sort_order, visible) '
        "VALUES ('Projects', 42, '+', 1, 1)"
    )
    conn.commit()
    stat_id = conn.execute('SELECT id FROM stats').fetchone()['id']
    _save_translation(conn, 'stats', stat_id, 'es', label='Proyectos')

    response = app.test_client().get('/api/v1/stats', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data'][0]['label'] == 'Proyectos'
    # Non-translated numeric fields pass through.
    assert body['data'][0]['value'] == 42


# ---------------------------------------------------------------------------
# /api/v1/certifications
# ---------------------------------------------------------------------------


def test_certifications_locale_overlay(multilocale_app):
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO certifications (name, issuer, description, sort_order, visible) '
        "VALUES ('Security+', 'CompTIA', 'English desc', 1, 1)"
    )
    conn.commit()
    cert_id = conn.execute('SELECT id FROM certifications').fetchone()['id']
    _save_translation(
        conn, 'certifications', cert_id, 'es', name='Seguridad+', description='Descripción ES'
    )

    response = app.test_client().get('/api/v1/certifications', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data'][0]['name'] == 'Seguridad+'
    assert body['data'][0]['description'] == 'Descripción ES'


# ---------------------------------------------------------------------------
# /api/v1/projects + /api/v1/projects/<slug>
# ---------------------------------------------------------------------------


def test_projects_list_locale_overlay(multilocale_app):
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO projects (title, slug, summary, description, sort_order, visible, has_detail_page) '
        "VALUES ('Ironclad', 'ironclad', 'English card', 'English body', 1, 1, 1)"
    )
    conn.commit()
    project_id = conn.execute('SELECT id FROM projects').fetchone()['id']
    _save_translation(
        conn,
        'projects',
        project_id,
        'es',
        title='Ironclad ES',
        summary='Resumen ES',
        description='Cuerpo ES',
    )

    response = app.test_client().get('/api/v1/projects', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data'][0]['title'] == 'Ironclad ES'
    assert body['data'][0]['summary'] == 'Resumen ES'


def test_project_detail_locale_overlay(multilocale_app):
    app, conn = multilocale_app
    conn.execute(
        'INSERT INTO projects (title, slug, summary, description, sort_order, visible, has_detail_page) '
        "VALUES ('Ironclad', 'ironclad', 'English card', 'English body', 1, 1, 1)"
    )
    conn.commit()
    project_id = conn.execute('SELECT id FROM projects').fetchone()['id']
    _save_translation(
        conn, 'projects', project_id, 'es', title='Ironclad ES', description='Cuerpo ES'
    )

    response = app.test_client().get('/api/v1/projects/ironclad', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data']['title'] == 'Ironclad ES'
    assert body['data']['description'] == 'Cuerpo ES'
    assert response.headers['Content-Language'] == 'es'


# ---------------------------------------------------------------------------
# /api/v1/blog + /api/v1/blog/<slug>
# ---------------------------------------------------------------------------


def _enable_blog(conn):
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('blog_enabled', 'true')")
    conn.commit()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def test_blog_list_locale_overlay(multilocale_app):
    app, conn = multilocale_app
    _enable_blog(conn)
    conn.execute(
        'INSERT INTO blog_posts (slug, title, summary, content, status, published_at) '
        "VALUES ('hello', 'Hello', 'English summary', '<p>English</p>', 'published', "
        "'2026-01-01T00:00:00Z')"
    )
    conn.commit()
    post_id = conn.execute('SELECT id FROM blog_posts').fetchone()['id']
    _save_translation(conn, 'blog_posts', post_id, 'es', title='Hola', summary='Resumen ES')

    response = app.test_client().get('/api/v1/blog', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data'][0]['title'] == 'Hola'
    assert body['data'][0]['summary'] == 'Resumen ES'
    assert response.headers['Content-Language'] == 'es'


def test_blog_detail_locale_overlay_includes_rendered_html(multilocale_app):
    """`rendered_html` reflects the overlaid content, not the original."""
    app, conn = multilocale_app
    _enable_blog(conn)
    conn.execute(
        'INSERT INTO blog_posts (slug, title, summary, content, content_format, status, '
        "published_at) VALUES ('hello', 'Hello', 'EN summary', '<p>English body</p>', "
        "'html', 'published', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    post_id = conn.execute('SELECT id FROM blog_posts').fetchone()['id']
    _save_translation(
        conn,
        'blog_posts',
        post_id,
        'es',
        title='Hola',
        content='<p>Cuerpo en español</p>',
    )

    response = app.test_client().get('/api/v1/blog/hello', headers={'Accept-Language': 'es'})
    body = _json(response)
    assert body['data']['title'] == 'Hola'
    assert body['data']['content'] == '<p>Cuerpo en español</p>'
    assert body['data']['rendered_html'] == '<p>Cuerpo en español</p>'


# ---------------------------------------------------------------------------
# Non-translatable endpoints should NOT carry locale headers
# ---------------------------------------------------------------------------


def test_portfolio_list_does_not_emit_content_language(multilocale_app):
    """`/portfolio` doesn't support translations, so no locale headers."""
    app, _ = multilocale_app
    response = app.test_client().get('/api/v1/portfolio', headers={'Accept-Language': 'es'})
    assert response.status_code == 200
    # Portfolio rows aren't translatable; the endpoint stays locale-neutral.
    assert 'Content-Language' not in response.headers


def test_testimonials_does_not_emit_content_language(multilocale_app):
    """Reviews aren't translatable either."""
    app, _ = multilocale_app
    response = app.test_client().get('/api/v1/testimonials', headers={'Accept-Language': 'es'})
    assert response.status_code == 200
    assert 'Content-Language' not in response.headers


# ---------------------------------------------------------------------------
# OpenAPI spec drift guard (Phase 16.5 already locks URL-map/spec parity,
# but Accept-Language is a header parameter — separate assertion)
# ---------------------------------------------------------------------------


def test_openapi_declares_accept_language_on_translatable_endpoints():
    """Every translatable read endpoint must document Accept-Language."""
    import os

    import yaml

    spec_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'docs', 'openapi.yaml')
    with open(spec_path) as f:
        spec = yaml.safe_load(f)

    expected_paths = (
        '/content/{slug}',
        '/services',
        '/stats',
        '/certifications',
        '/projects',
        '/projects/{slug}',
        '/blog',
        '/blog/{slug}',
    )
    for path in expected_paths:
        params = spec['paths'][path]['get'].get('parameters', [])
        refs = [p.get('$ref', '') for p in params if isinstance(p, dict)]
        assert any('AcceptLanguage' in ref for ref in refs), (
            f'{path} GET does not reference #/components/parameters/AcceptLanguage'
        )

    # And the parameter component itself is defined.
    assert 'AcceptLanguage' in spec['components']['parameters']
    accept_lang = spec['components']['parameters']['AcceptLanguage']
    assert accept_lang['in'] == 'header'
    assert accept_lang['name'] == 'Accept-Language'
