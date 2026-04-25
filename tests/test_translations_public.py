"""
Phase 15.4 — Public Translation Rendering + SEO

Verifies the locale overlay wired into public routes:
    * Landing page / services / projects / certifications respect the
      active locale and fall back to the default-locale row when no
      translation exists.
    * Blog listing and single-post pages receive translated titles /
      summaries / content when the active locale has a translation row.
    * Sitemap emits ``<xhtml:link rel="alternate">`` entries when more
      than one locale is configured and stays clean in single-locale
      deployments.
    * ``base.html`` carries ``og:locale`` and ``og:locale:alternate``
      meta tags.
    * ``/blog/feed.xml?lang=<code>`` returns the locale-specific feed
      with overlaid titles and the matching ``<language>`` channel tag.

The translation service unit tests cover the overlay primitives; this
file focuses on the route-level integration.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def multilocale_db(app):
    """Return an open connection with ``available_locales = en,es``.

    Callers are expected to seed any content blocks / services / etc.
    they need; this fixture only flips the setting so the base template
    renders hreflang / language-switcher markup and the sitemap emits
    alternates.
    """
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('available_locales', 'en,es'),
    )
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('default_locale', 'en'),
    )
    conn.commit()

    # Cache must be cleared after writing settings — the context
    # processor reads through the 30 s TTL cache.
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    yield conn
    conn.close()


def _seed_service(conn, title, description, sort_order=1, visible=1):
    conn.execute(
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        'VALUES (?, ?, ?, ?, ?)',
        (title, description, '', sort_order, visible),
    )
    conn.commit()
    return conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']


def _seed_translation(conn, parent_table, parent_id, locale, **fields):
    from app.services.translations import save_translation

    save_translation(conn, parent_table, parent_id, locale, **fields)
    conn.commit()


# ---------------------------------------------------------------------------
# Overlay wrappers — unit level
# ---------------------------------------------------------------------------


def test_services_overlay_short_circuits_on_default_locale(app, multilocale_db):
    """When locale == default, the overlay returns the raw row verbatim.

    Short-circuit path is important because ``get_all_translated``
    costs a LEFT JOIN — irrelevant for single-locale deployments.
    """
    service_id = _seed_service(multilocale_db, 'Consulting', 'English desc')

    from app.services.translations import get_visible_services_for_locale

    rows = get_visible_services_for_locale(multilocale_db, 'en', 'en')
    assert len(rows) == 1
    assert rows[0]['title'] == 'Consulting'
    # sqlite3.Row support attribute access — prove we didn't convert to dict
    assert isinstance(rows[0], sqlite3.Row)
    assert rows[0]['id'] == service_id


def test_services_overlay_applies_translation(app, multilocale_db):
    """An ``es`` translation should surface on the Spanish request."""
    service_id = _seed_service(multilocale_db, 'Consulting', 'English desc')
    _seed_translation(
        multilocale_db,
        'services',
        service_id,
        'es',
        title='Consultoría',
        description='Descripción en español',
    )

    from app.services.translations import get_visible_services_for_locale

    rows = get_visible_services_for_locale(multilocale_db, 'es', 'en')
    assert len(rows) == 1
    assert rows[0]['title'] == 'Consultoría'
    assert rows[0]['description'] == 'Descripción en español'


def test_services_overlay_falls_back_when_translation_missing(app, multilocale_db):
    """Without an ``es`` row, the overlay keeps the default-locale value."""
    _seed_service(multilocale_db, 'Consulting', 'English desc')

    from app.services.translations import get_visible_services_for_locale

    rows = get_visible_services_for_locale(multilocale_db, 'es', 'en')
    assert len(rows) == 1
    assert rows[0]['title'] == 'Consulting'
    assert rows[0]['description'] == 'English desc'


def test_stats_overlay_applies_translation(app, multilocale_db):
    """Stats overlay follows the same pattern as services."""
    multilocale_db.execute(
        'INSERT INTO stats (label, value, suffix, sort_order, visible) VALUES (?, ?, ?, ?, ?)',
        ('Projects', 42, '+', 1, 1),
    )
    multilocale_db.commit()
    stat_id = multilocale_db.execute('SELECT id FROM stats').fetchone()['id']

    _seed_translation(
        multilocale_db,
        'stats',
        stat_id,
        'es',
        label='Proyectos',
    )

    from app.services.translations import get_visible_stats_for_locale

    rows = get_visible_stats_for_locale(multilocale_db, 'es', 'en')
    assert rows[0]['label'] == 'Proyectos'
    # Non-translated fields (numeric value) survive untouched.
    assert rows[0]['value'] == 42


def test_content_block_overlay_by_slug(app, multilocale_db):
    """``get_content_block_for_locale`` resolves by slug, then translates."""
    multilocale_db.execute(
        'INSERT INTO content_blocks (slug, title, content, plain_text) VALUES (?, ?, ?, ?)',
        ('about', 'About', '<p>English body</p>', 'English body'),
    )
    multilocale_db.commit()
    block_id = multilocale_db.execute(
        "SELECT id FROM content_blocks WHERE slug = 'about'"
    ).fetchone()['id']

    _seed_translation(
        multilocale_db,
        'content_blocks',
        block_id,
        'es',
        title='Sobre mí',
        content='<p>Cuerpo en español</p>',
    )

    from app.services.translations import get_content_block_for_locale

    result = get_content_block_for_locale(multilocale_db, 'about', 'es', 'en')
    assert result is not None
    assert result['title'] == 'Sobre mí'
    assert result['content'] == '<p>Cuerpo en español</p>'

    # Unknown slug returns None, not a partial dict
    assert get_content_block_for_locale(multilocale_db, 'missing', 'es', 'en') is None


def test_og_locale_mapping_known_codes():
    """Known ISO 639-1 codes map to their BCP 47 Open Graph form."""
    from app.services.translations import og_locale

    assert og_locale('en') == 'en_US'
    assert og_locale('es') == 'es_ES'
    assert og_locale('fr') == 'fr_FR'
    assert og_locale('ja') == 'ja_JP'


def test_og_locale_normalises_region_form():
    """Region-qualified inputs are normalised to underscore case."""
    from app.services.translations import og_locale

    assert og_locale('pt-BR') == 'pt_BR'
    assert og_locale('pt_br') == 'pt_BR'
    assert og_locale('zh-TW') == 'zh_TW'


def test_og_locale_handles_unknown_code():
    """Unknown two-letter codes fall back to ``xx_XX`` form."""
    from app.services.translations import og_locale

    assert og_locale('yz') == 'yz_YZ'


def test_og_locale_empty_returns_default():
    """Empty / falsy input returns the English default, not a crash."""
    from app.services.translations import og_locale

    assert og_locale('') == 'en_US'
    assert og_locale(None) == 'en_US'  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Route-level integration
# ---------------------------------------------------------------------------


def test_landing_page_renders_translated_service(app, multilocale_db):
    """A Spanish session should see the translated service on ``/``."""
    service_id = _seed_service(multilocale_db, 'Consulting', 'English desc')
    _seed_translation(multilocale_db, 'services', service_id, 'es', title='Consultoría')

    with app.test_client() as c:
        c.get('/set-locale/es')  # persists in session
        response = c.get('/')

    assert response.status_code == 200
    html = response.data.decode()
    assert 'Consultoría' in html
    # Original English title should NOT be present — the overlay replaces it.
    assert 'Consulting' not in html


def test_landing_page_falls_back_when_no_translation(app, multilocale_db):
    """With no ``es`` row, the Spanish request still gets the English copy."""
    _seed_service(multilocale_db, 'Consulting', 'English desc')

    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/')

    assert response.status_code == 200
    assert b'Consulting' in response.data


def test_services_page_uses_overlay(app, multilocale_db):
    """``/services`` goes through the locale wrapper."""
    service_id = _seed_service(multilocale_db, 'Security Reviews', 'English desc')
    _seed_translation(
        multilocale_db,
        'services',
        service_id,
        'es',
        title='Revisiones de seguridad',
    )

    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/services')

    assert response.status_code == 200
    assert 'Revisiones de seguridad' in response.data.decode()


def test_certifications_page_uses_overlay(app, multilocale_db):
    """``/certifications`` goes through the locale wrapper.

    Migration 011 was realigned so the ``certification_translations``
    table uses ``name`` (matching the parent) — the overlay's column
    pairing COALESCE(t.name, s.name) needs identical names on both
    sides.
    """
    multilocale_db.execute(
        'INSERT INTO certifications (name, issuer, description, sort_order, visible) '
        'VALUES (?, ?, ?, ?, ?)',
        ('Security+', 'CompTIA', 'English description', 1, 1),
    )
    multilocale_db.commit()
    cert_id = multilocale_db.execute('SELECT id FROM certifications').fetchone()[0]

    _seed_translation(
        multilocale_db,
        'certifications',
        cert_id,
        'es',
        name='Seguridad+',
        description='Descripción en español',
    )

    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/certifications')

    assert response.status_code == 200
    html = response.data.decode()
    assert 'Seguridad+' in html


def test_projects_page_uses_overlay(app, multilocale_db):
    """``/projects`` goes through the locale wrapper (summary field)."""
    multilocale_db.execute(
        'INSERT INTO projects (title, slug, summary, description, sort_order, visible, has_detail_page) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        ('Ironclad', 'ironclad', 'English card blurb', 'Original long body', 1, 1, 0),
    )
    multilocale_db.commit()
    project_id = multilocale_db.execute('SELECT id FROM projects').fetchone()[0]

    _seed_translation(
        multilocale_db,
        'projects',
        project_id,
        'es',
        summary='Resumen en español',
    )

    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/projects')

    assert response.status_code == 200
    html = response.data.decode()
    assert 'Resumen en español' in html


def test_project_detail_uses_overlay(app, multilocale_db):
    """``/projects/<slug>`` swaps in the translated description."""
    multilocale_db.execute(
        'INSERT INTO projects (title, slug, description, sort_order, visible, has_detail_page) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        ('Ironclad', 'ironclad', 'English body', 1, 1, 1),
    )
    multilocale_db.commit()
    project_id = multilocale_db.execute('SELECT id FROM projects').fetchone()[0]

    _seed_translation(
        multilocale_db,
        'projects',
        project_id,
        'es',
        title='Ironclad ES',
        description='Cuerpo en español',
    )

    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/projects/ironclad')

    assert response.status_code == 200
    html = response.data.decode()
    assert 'Cuerpo en español' in html


# ---------------------------------------------------------------------------
# Sitemap hreflang alternates
# ---------------------------------------------------------------------------


def test_sitemap_emits_xhtml_alternates_when_multilocale(client, multilocale_db):
    """The sitemap should carry ``xhtml:link`` entries under multilocale."""
    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    body = response.data.decode()
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in body
    assert 'hreflang="en"' in body
    assert 'hreflang="es"' in body
    assert 'hreflang="x-default"' in body
    # Every static path should get an alternate (pick one to verify)
    assert (
        '<xhtml:link rel="alternate" hreflang="es" href="http://localhost/portfolio?lang=es"'
        in body
    )


def test_sitemap_omits_xhtml_in_single_locale_deployments(client):
    """Single-locale deployments should stay clean (no xhtml namespace)."""
    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    body = response.data.decode()
    assert 'xmlns:xhtml' not in body
    assert 'hreflang' not in body


# ---------------------------------------------------------------------------
# Open Graph locale tags
# ---------------------------------------------------------------------------


def test_og_locale_meta_tags_on_landing(client, multilocale_db):
    """``og:locale`` should reflect the active locale; alternates cover the rest."""
    response = client.get('/')
    html = response.data.decode()
    assert '<meta property="og:locale" content="en_US">' in html
    # The other available locale emits as an alternate
    assert '<meta property="og:locale:alternate" content="es_ES">' in html


def test_og_locale_meta_on_spanish_session(app, multilocale_db):
    """After switching to Spanish, ``og:locale`` should flip to ``es_ES``."""
    with app.test_client() as c:
        c.get('/set-locale/es')
        response = c.get('/')

    html = response.data.decode()
    assert '<meta property="og:locale" content="es_ES">' in html
    # English is now the alternate, not the primary.
    assert '<meta property="og:locale:alternate" content="en_US">' in html


def test_og_locale_single_locale_omits_alternates(client):
    """Single-locale sites shouldn't emit empty alternate stanzas."""
    response = client.get('/')
    html = response.data.decode()
    assert '<meta property="og:locale" content="en_US">' in html
    assert 'og:locale:alternate' not in html


# ---------------------------------------------------------------------------
# Blog RSS + per-locale feed
# ---------------------------------------------------------------------------


def _enable_blog(conn):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('blog_enabled', 'true')")
    conn.commit()
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def _seed_post(conn, title, summary, slug='hello'):
    conn.execute(
        'INSERT INTO blog_posts (slug, title, summary, content, status, published_at) '
        "VALUES (?, ?, ?, ?, 'published', '2026-01-01T00:00:00Z')",
        (slug, title, summary, '<p>Body</p>'),
    )
    conn.commit()
    return conn.execute('SELECT id FROM blog_posts WHERE slug = ?', (slug,)).fetchone()['id']


def test_blog_feed_default_locale_channel_language(client, multilocale_db):
    """Without ``?lang``, the channel ``<language>`` matches the default."""
    _enable_blog(multilocale_db)
    _seed_post(multilocale_db, 'Hello', 'Summary')

    response = client.get('/blog/feed.xml')
    assert response.status_code == 200
    body = response.data.decode()
    assert '<language>en</language>' in body
    assert '<title>Hello</title>' in body


def test_blog_feed_locale_query_overlays_titles(client, multilocale_db):
    """``?lang=es`` should surface translated titles + tag the channel."""
    _enable_blog(multilocale_db)
    post_id = _seed_post(multilocale_db, 'Hello', 'Summary')
    _seed_translation(
        multilocale_db,
        'blog_posts',
        post_id,
        'es',
        title='Hola',
        summary='Resumen',
    )

    response = client.get('/blog/feed.xml?lang=es')
    body = response.data.decode()
    assert '<language>es</language>' in body
    assert '<title>Hola</title>' in body
    # The self-referential atom:link should also carry the lang param.
    assert 'feed.xml?lang=es' in body


def test_blog_feed_unknown_locale_falls_back_to_default(client, multilocale_db):
    """Unknown ``?lang`` values fall back without 500s or silent bugs."""
    _enable_blog(multilocale_db)
    _seed_post(multilocale_db, 'Hello', 'Summary')

    response = client.get('/blog/feed.xml?lang=xx')
    assert response.status_code == 200
    body = response.data.decode()
    assert '<language>en</language>' in body


def test_blog_post_page_emits_post_locale_alternates(app, multilocale_db):
    """Single-post pages should list ``og:locale:alternate`` per translation.

    The landing page gets alternates from the site-wide
    ``available_locales`` setting; a blog post scope is narrower — only
    locales that actually have a translation row for THIS post count.
    """
    _enable_blog(multilocale_db)
    post_id = _seed_post(multilocale_db, 'Hello', 'Summary')
    _seed_translation(
        multilocale_db,
        'blog_posts',
        post_id,
        'es',
        title='Hola',
        summary='Resumen',
    )

    with app.test_client() as c:
        response = c.get('/blog/hello')

    assert response.status_code == 200
    html = response.data.decode()
    # Primary locale is the session locale ('en' on first visit).
    assert '<meta property="og:locale" content="en_US">' in html
    # The per-post block REPLACES the default alternates block, so we
    # should see only the locales with translation rows (not the
    # site-wide ``available_locales`` list).
    assert '<meta property="og:locale:alternate" content="es_ES">' in html


# ---------------------------------------------------------------------------
# Concurrent save_translation — race regression (#122)
# ---------------------------------------------------------------------------


def test_save_translation_concurrent_does_not_500_on_race(app, multilocale_db):
    """Two threads saving the same (parent_id, locale) must not raise.

    Before #122, ``save_translation`` ran SELECT-then-INSERT/UPDATE
    without a transaction. If two requests for the same translation
    landed at the same time, both observed "no existing row", both
    attempted the INSERT, and the loser tripped the
    ``UNIQUE(parent_id, locale)`` constraint with an
    ``IntegrityError`` 500.

    The fix wraps the read+write in a ``BEGIN IMMEDIATE`` block and
    retries once as an UPDATE if the INSERT still loses the race.
    After both threads return:
      * Neither raised.
      * Exactly one row exists for ``(service_id, 'es')``.
      * The surviving title is one of the two values submitted (we
        don't assert which — either ordering is correct).
    """
    service_id = _seed_service(multilocale_db, 'Consulting', 'English desc')

    db_path = app.config['DATABASE_PATH']
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _worker(title_value: str) -> None:
        from app.services.translations import save_translation

        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA busy_timeout = 5000')
        try:
            # Both threads finish setup before either calls the
            # service — maximises the window the race is built for.
            barrier.wait(timeout=10)
            save_translation(
                conn,
                'services',
                service_id,
                'es',
                title=title_value,
                description=f'desc-{title_value}',
            )
        except Exception as exc:  # noqa: BLE001 — surface failures via list
            errors.append(exc)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=_worker, args=('Consultoría-A',)),
        threading.Thread(target=_worker, args=('Consultoría-B',)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [], f'concurrent save_translation raised: {errors}'

    rows = multilocale_db.execute(
        'SELECT title FROM service_translations WHERE service_id = ? AND locale = ?',
        (service_id, 'es'),
    ).fetchall()
    # The UNIQUE(parent_id, locale) constraint guarantees at most one
    # row; the fix guarantees at least one. Exactly one is the
    # contract.
    assert len(rows) == 1
    assert rows[0]['title'] in {'Consultoría-A', 'Consultoría-B'}
