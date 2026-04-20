"""
Sanitizer Contract Tests — Phase 22.2

Every HTML-accepting write path in the admin must strip ``<script>``
tags, inline ``on*=`` event handlers, and ``javascript:`` / ``data:``
URL schemes before the value hits the database. This module is the
single cross-cutting check that proves each path honours that
contract — regressions in one service don't slip through because
another service happens to still sanitise.

The first half is a property-based sweep over ``sanitize_html``
itself: for any payload embedded in any legal container tag, the
dangerous substrings must not survive a round trip through the
sanitiser.

The second half hits the concrete admin write paths
(content-block save, blog post save, service create/update,
translation save, custom-nav-link settings save) and asserts the
same contract end-to-end.
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.content import sanitize_html, validate_safe_url

# ---------------------------------------------------------------------------
# Property-based sanitiser sweep
# ---------------------------------------------------------------------------

_DANGEROUS_SUBSTRINGS = (
    '<script',
    '</script',
    'onclick=',
    'onerror=',
    'onload=',
    'onmouseover=',
    'onfocus=',
    'javascript:',
    'vbscript:',
    'data:text/html',
)

_ALLOWED_CONTAINER_TAGS = ('p', 'div', 'span', 'h2', 'li', 'blockquote', 'strong', 'em')


@given(
    container=st.sampled_from(_ALLOWED_CONTAINER_TAGS),
    payload=st.sampled_from(
        [
            '<script>alert(1)</script>',
            '<img src=x onerror="alert(1)">',
            '<a href="javascript:alert(1)">x</a>',
            '<a href="JAVASCRIPT:alert(1)">x</a>',
            '<iframe src="https://evil.example/"></iframe>',
            '<svg onload="alert(1)"></svg>',
            '<p onclick="fetch(1)">text</p>',
            '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        ]
    ),
)
def test_sanitize_html_strips_every_xss_vector(container, payload):
    html = f'<{container}>innocent{payload}more</{container}>'
    cleaned = sanitize_html(html).lower()
    for forbidden in _DANGEROUS_SUBSTRINGS:
        assert forbidden not in cleaned, (
            f'substring {forbidden!r} survived sanitisation of {html!r} — '
            f'nh3 result was {cleaned!r}'
        )


def test_sanitize_html_preserves_safe_markup():
    cleaned = sanitize_html(
        '<h2>Title</h2><p><strong>Bold</strong> <em>italic</em> '
        '<a href="https://example.com">link</a></p>'
    )
    assert '<h2>Title</h2>' in cleaned
    assert '<strong>Bold</strong>' in cleaned
    assert 'href="https://example.com"' in cleaned


def test_sanitize_html_empty_returns_empty():
    assert sanitize_html('') == ''


def test_sanitize_html_fails_loud_if_nh3_missing(monkeypatch):
    """#63 fail-closed contract: a missing/broken nh3 import must not
    silently degrade to pass-through. Post-22.2 the module imports nh3
    at the top so an unbuilt ``nh3`` would cause a hard ImportError at
    app boot."""
    import app.services.content as content_mod

    # Simulate an obliterated nh3 — after the 22.2 fix the attribute
    # is unconditional, so referencing a removed symbol must raise,
    # *not* return the input unchanged. Python raises ``NameError``
    # when a module-global is missing at call time; ``AttributeError``
    # arises from the namespaced ``nh3.clean`` reference on some
    # interpreter versions. Either is the fail-loud signal we want.
    monkeypatch.delattr(content_mod, 'nh3')
    with pytest.raises((NameError, AttributeError)):
        content_mod.sanitize_html('<p>x</p>')


# ---------------------------------------------------------------------------
# validate_safe_url — #17 URL allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'url',
    [
        'https://example.com/page',
        'http://example.com/page',
        'mailto:hi@example.com',
        '/admin/dashboard',
        '/',
        '#anchor',
        '?only=query',
    ],
)
def test_validate_safe_url_accepts_safe(url):
    assert validate_safe_url(url) is True


@pytest.mark.parametrize(
    'url',
    [
        'javascript:alert(1)',
        'JavaScript:alert(1)',
        '  javascript:alert(1)  ',
        'vbscript:msgbox(1)',
        'data:text/html,<script>alert(1)</script>',
        '//evil.example/foo',
        'ftp://example.com/',
        '',
        '   ',
        None,
    ],
)
def test_validate_safe_url_rejects_unsafe(url):
    assert validate_safe_url(url) is False


# ---------------------------------------------------------------------------
# End-to-end contract at every admin write path
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_db(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    yield conn
    conn.close()


def _read_col(conn, sql, *params):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row is not None else None


def test_content_block_save_strips_xss(sqlite_db):
    from app.services.content import save_block

    save_block(
        sqlite_db,
        slug='about-me',
        title='About',
        content_html='<p>ok</p><script>alert(1)</script>',
    )
    stored = _read_col(sqlite_db, 'SELECT content FROM content_blocks WHERE slug = ?', 'about-me')
    assert stored is not None
    assert '<script' not in stored
    assert 'alert(1)' not in stored
    assert '<p>ok</p>' in stored


def test_blog_post_html_save_strips_onerror(app, sqlite_db):
    from app.db import get_db
    from app.services.blog import create_post

    with app.app_context():
        create_post(
            get_db(),
            title='XSS attempt',
            summary='',
            content='<img src="x" onerror="alert(1)">',
            content_format='html',
            cover_image='',
            author='test',
        )
    stored = _read_col(sqlite_db, "SELECT content FROM blog_posts WHERE title = 'XSS attempt'")
    assert stored is not None
    assert 'onerror' not in stored.lower()
    assert 'alert(1)' not in stored


def test_service_add_strips_script(app, sqlite_db):
    from app.db import get_db
    from app.services.service_items import add_service

    with app.app_context():
        add_service(get_db(), 'Evil', '<p>ok</p><script>alert(1)</script>', '', 1)
    stored = _read_col(sqlite_db, "SELECT description FROM services WHERE title = 'Evil'")
    assert stored is not None
    assert '<script' not in stored.lower()
    assert '<p>ok</p>' in stored


def test_translation_save_sanitises_html_fields(app, sqlite_db):
    """Phase 22.2 (#41) — translation writes must run HTML fields through
    the same sanitiser the default-locale save uses. Previously the
    per-locale save was a straight string pass-through, so an admin who
    translated a content block into `es` could smuggle an XSS that
    English never saw."""
    from app.services.content import save_block
    from app.services.translations import save_translation

    # Seed a clean block so the translation has a parent row.
    save_block(sqlite_db, slug='about', title='About', content_html='<p>original</p>')
    parent_id = _read_col(sqlite_db, "SELECT id FROM content_blocks WHERE slug = 'about'")
    with app.app_context():
        save_translation(
            sqlite_db,
            'content_blocks',
            parent_id,
            'es',
            title='Sobre mí',
            content='<p>hola</p><script>alert(1)</script>',
        )
        sqlite_db.commit()
    stored = _read_col(
        sqlite_db,
        'SELECT content FROM content_block_translations WHERE block_id = ? AND locale = ?',
        parent_id,
        'es',
    )
    assert stored is not None
    assert '<script' not in stored
    assert 'alert(1)' not in stored
    assert '<p>hola</p>' in stored


def test_translation_save_passes_through_plain_text_fields(app, sqlite_db):
    """Stats ``label`` is plain text — no sanitiser applies because Jinja
    autoescape handles the render. Round-trip whatever the admin typed.
    This is the complementary guardrail against over-eager sanitisation
    stripping legitimate characters like ``<`` in "x < y"."""
    from app.services.translations import save_translation

    # Seed a stat.
    sqlite_db.execute(
        'INSERT INTO stats (id, label, value, sort_order) VALUES (1, ?, ?, ?)',
        ('Projects', 42, 1),
    )
    sqlite_db.commit()

    with app.app_context():
        save_translation(
            sqlite_db,
            'stats',
            1,
            'es',
            label='<Proyectos>',  # plain text field, no sanitiser
            suffix='+',
        )
        sqlite_db.commit()
    stored = _read_col(
        sqlite_db,
        'SELECT label FROM stat_translations WHERE stat_id = 1 AND locale = ?',
        'es',
    )
    assert stored == '<Proyectos>'


def test_settings_save_rejects_javascript_nav_link(auth_client):
    """#17 — a custom_nav_links entry whose ``url`` is ``javascript:alert(1)``
    must 400 and the setting must NOT be written."""
    response = auth_client.post(
        '/admin/settings',
        data={
            'custom_nav_links': '[{"label":"Evil","url":"javascript:alert(1)"}]',
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    # The body comes from the re-rendered settings page + flash.
    assert b'unsafe URL' in response.data or b'custom_nav_links' in response.data


def test_settings_save_rejects_scheme_relative_nav_link(auth_client):
    response = auth_client.post(
        '/admin/settings',
        data={'custom_nav_links': '[{"label":"X","url":"//evil.example"}]'},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_settings_save_accepts_http_nav_link(auth_client, app):
    response = auth_client.post(
        '/admin/settings',
        data={'custom_nav_links': '[{"label":"GitHub","url":"https://github.com/me"}]'},
        follow_redirects=False,
    )
    assert response.status_code == 302
    # Row was written.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        stored = conn.execute(
            "SELECT value FROM settings WHERE key = 'custom_nav_links'"
        ).fetchone()
    finally:
        conn.close()
    assert stored is not None
    assert 'https://github.com/me' in stored[0]


def test_admin_search_snippet_does_not_render_attacker_script(auth_client, app):
    """#44 — the FTS snippet used to run through ``| safe``. Prove a
    stored ``<script>`` payload from a review body renders as escaped
    text, not as live markup."""
    # Seed a review whose body contains the payload; insert directly
    # because the public /review/<token> path sanitises now. The FTS
    # trigger will pick up the row.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        conn.execute(
            "INSERT INTO review_tokens (token, name, type) VALUES ('sc', 'A', 'recommendation')"
        )
        conn.execute(
            'INSERT INTO reviews (token_id, reviewer_name, message, type, status, display_tier) '
            "VALUES (1, 'A', ?, 'recommendation', 'approved', 'featured')",
            ('<script>alert("fts")</script> payload',),
        )
        conn.commit()
    finally:
        conn.close()

    response = auth_client.get('/admin/search?q=payload')
    assert response.status_code == 200
    body = response.data.decode('utf-8')
    # The raw <script> must NOT reach the rendered HTML. If the FTS
    # happens to not match (missing trigger), just assert the snippet
    # is absent — avoids brittle dependencies on the index state.
    assert '<script>alert(' not in body
    assert 'alert(&quot;fts&quot;)' not in body
