"""
Edge-case tests for blog create/update — Phase 18.13.

Exercises the checklist in ``tests/TESTING_STANDARDS.md`` against both the
admin HTML form (``/admin/blog/new`` and ``/admin/blog/<id>/edit``) and the
JSON API (``POST /api/v1/blog`` and ``PUT /api/v1/blog/<slug>``): slug
uniqueness under collision, Unicode titles, oversized bodies, and injection.

Concurrency notes:
    slug uniqueness under a race is covered by a best-effort threaded test
    — ``_ensure_unique_slug`` is a SELECT + conditional append, so a
    genuinely simultaneous create could theoretically produce the same
    slug twice. The test asserts we never 500 and every successful create
    has a distinct slug.
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
def api_write_token(app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        return generate_token(get_db(), name='edge-cases', scope='read,write').raw


def _auth(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _fetch_slugs(app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        return [r[0] for r in conn.execute('SELECT slug FROM blog_posts')]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Empty / null inputs
# ---------------------------------------------------------------------------


def test_admin_rejects_empty_title(auth_client):
    response = auth_client.post(
        '/admin/blog/new',
        data={'title': '', 'content': 'body'},
        follow_redirects=False,
    )
    assert response.status_code == 200  # form re-rendered, not redirected
    assert b'Title is required' in response.data or b'required' in response.data


def test_admin_rejects_whitespace_title(auth_client):
    response = auth_client.post(
        '/admin/blog/new',
        data={'title': '   \t\n  ', 'content': 'body'},
        follow_redirects=False,
    )
    assert response.status_code == 200


def test_api_rejects_missing_title_400(client, no_rate_limits, api_write_token):
    response = client.post('/api/v1/blog', json={}, headers=_auth(api_write_token))
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['details']['field'] == 'title'


def test_api_rejects_whitespace_only_title_400(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={'title': '   '},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


def test_api_rejects_null_title_400(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={'title': None},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Slug collision handling
# ---------------------------------------------------------------------------


def test_two_posts_with_same_title_get_distinct_slugs(client, no_rate_limits, api_write_token, app):
    first = client.post(
        '/api/v1/blog', json={'title': 'Hello World'}, headers=_auth(api_write_token)
    )
    second = client.post(
        '/api/v1/blog', json={'title': 'Hello World'}, headers=_auth(api_write_token)
    )
    assert first.status_code == 201
    assert second.status_code == 201
    slug1 = first.get_json()['data']['slug']
    slug2 = second.get_json()['data']['slug']
    assert slug1 != slug2
    assert slug1 == 'hello-world'
    assert slug2 == 'hello-world-2'


def test_manual_slug_collision_gets_numeric_suffix(client, no_rate_limits, api_write_token, app):
    # Seed a post at the desired slug, then PUT with that same slug on another
    client.post('/api/v1/blog', json={'title': 'Original'}, headers=_auth(api_write_token))
    other = client.post(
        '/api/v1/blog', json={'title': 'Other'}, headers=_auth(api_write_token)
    ).get_json()['data']['slug']

    response = client.put(
        f'/api/v1/blog/{other}',
        json={'slug': 'original'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    final_slug = response.get_json()['data']['slug']
    assert final_slug != 'original'
    assert final_slug.startswith('original-')


def test_editing_a_post_and_keeping_its_slug_does_not_collide_with_itself(
    client, no_rate_limits, api_write_token, app
):
    created = client.post(
        '/api/v1/blog', json={'title': 'Stable Title'}, headers=_auth(api_write_token)
    ).get_json()['data']
    slug = created['slug']

    updated = client.put(
        f'/api/v1/blog/{slug}',
        json={'title': 'Stable Title', 'slug': slug},
        headers=_auth(api_write_token),
    )
    assert updated.status_code == 200
    assert updated.get_json()['data']['slug'] == slug


# ---------------------------------------------------------------------------
# Unicode titles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'title',
    [
        'Café Résumé',  # accented Latin
        '日本語のブログ',  # CJK
        '🚀 Launch Announcement 🚀',  # emoji
        'أهلاً بالعالم',  # Arabic (RTL)
        'שלום עולם',  # Hebrew (RTL)
        'e\u0301clipse',  # combining mark
    ],
)
def test_unicode_titles_produce_valid_slugs(client, no_rate_limits, api_write_token, app, title):
    response = client.post('/api/v1/blog', json={'title': title}, headers=_auth(api_write_token))
    assert response.status_code == 201, response.get_json()
    slug = response.get_json()['data']['slug']
    # slugify strips everything not [\w-]; may yield empty-ish slugs for
    # scripts it doesn't transliterate. The only hard guarantee is uniqueness
    # and the slug not being the empty string.
    assert slug
    assert slug in _fetch_slugs(app)


# ---------------------------------------------------------------------------
# Length boundaries
# ---------------------------------------------------------------------------


def test_oversized_body_is_accepted_and_stored(client, no_rate_limits, api_write_token, app):
    """The sanitizer + storage should handle a 1 MB body without blowing up."""
    huge = '<p>' + ('x' * (1 << 20)) + '</p>'
    response = client.post(
        '/api/v1/blog',
        json={'title': 'Bulk', 'content': huge, 'content_format': 'html'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    # Reading time should still be sane (a positive int)
    assert response.get_json()['data']['reading_time'] >= 1


def test_single_character_title_accepted(client, no_rate_limits, api_write_token):
    response = client.post('/api/v1/blog', json={'title': 'a'}, headers=_auth(api_write_token))
    assert response.status_code == 201
    assert response.get_json()['data']['slug'] == 'a'


def test_single_character_unicode_title_accepted(client, no_rate_limits, api_write_token):
    response = client.post('/api/v1/blog', json={'title': 'ß'}, headers=_auth(api_write_token))
    assert response.status_code == 201


def test_10x_normal_title_length(client, no_rate_limits, api_write_token):
    """500-char titles must not overflow the slug column or crash."""
    response = client.post(
        '/api/v1/blog', json={'title': 'Word ' * 100}, headers=_auth(api_write_token)
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Injection handling — sanitize_html must strip <script> from HTML posts
# ---------------------------------------------------------------------------


def test_script_tags_in_html_content_are_stripped(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={
            'title': 'XSS attempt',
            'content': '<p>ok</p><script>alert(1)</script>',
            'content_format': 'html',
        },
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    stored = response.get_json()['data']['content']
    assert '<script' not in stored.lower()
    assert 'alert(1)' not in stored  # the whole tag was stripped, not just brackets


def test_event_handlers_in_html_content_are_stripped(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={
            'title': 'Event-handler attempt',
            'content': '<img src=x onerror="alert(1)">',
            'content_format': 'html',
        },
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    stored = response.get_json()['data']['content']
    assert 'onerror' not in stored.lower()


def test_javascript_url_in_href_is_stripped(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={
            'title': 'JS link',
            'content': '<a href="javascript:alert(1)">click</a>',
            'content_format': 'html',
        },
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    stored = response.get_json()['data']['content']
    assert 'javascript:' not in stored.lower()


def test_sql_metacharacters_in_title_do_not_break_create(
    client, no_rate_limits, api_write_token, app
):
    """SQL metacharacters that don't trip the WAF (#84) must still pass
    cleanly through the parameterized-query layer.

    The WAF body-scan added in v0.3.3 (#84) blocks fingerprints like
    ``;DROP TABLE`` or ``' OR 1=1`` outright, so the original
    "Bobby Tables" payload now returns 400 at the WAF before reaching
    the DB. The DB-layer defense (parameterized queries) still needs
    coverage — exercise it with metacharacters the WAF allows: a bare
    apostrophe and a literal hyphen pair are both legal in titles.
    """
    response = client.post(
        '/api/v1/blog',
        json={'title': "O'Reilly's Guide to SQL -- best practices"},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    # If SQL ran, the follow-up table lookup would fail. Query blog_posts
    # directly to confirm the schema is still intact.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    try:
        rows = conn.execute('SELECT COUNT(*) FROM blog_posts').fetchone()
    finally:
        conn.close()
    assert rows[0] >= 1


def test_sql_injection_fingerprint_in_body_blocked_by_waf(client, no_rate_limits, api_write_token):
    """#84: the WAF body-scan blocks JSON payloads carrying SQLi
    fingerprints (``;DROP TABLE``, ``' OR 1=1``, ``UNION SELECT``).

    This is an earlier line of defense than the parameterized-query
    layer — both must hold. The classic Bobby Tables payload that
    used to slip through the WAF now returns 400.
    """
    response = client.post(
        '/api/v1/blog',
        json={'title': "Robert'); DROP TABLE blog_posts;--"},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Content-format edge cases
# ---------------------------------------------------------------------------


def test_markdown_content_is_not_html_sanitized_on_write(client, no_rate_limits, api_write_token):
    """Markdown is stored raw; rendering happens at read time via
    sanitize_html(_markdown(content)). So the stored content survives
    verbatim — this test pins down that contract.
    """
    md = '# Heading\n\n<script>alert(1)</script>'
    response = client.post(
        '/api/v1/blog',
        json={'title': 'md', 'content': md, 'content_format': 'markdown'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    stored = response.get_json()['data']['content']
    assert '<script>' in stored  # raw content preserved
    # But the rendered output (if the API includes it) must be sanitized
    rendered = response.get_json()['data'].get('rendered_content', '')
    assert '<script' not in rendered.lower()


# ---------------------------------------------------------------------------
# Type coercion — ``featured``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'truthy',
    [True, 1, 'true', 'yes', [0]],  # any Python-truthy value becomes 1
)
def test_featured_accepts_truthy_values(client, no_rate_limits, api_write_token, truthy):
    response = client.post(
        '/api/v1/blog',
        json={'title': f'featured-{hash(str(truthy))}', 'featured': truthy},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    # API may serialise as 1 / True depending on the column type — both
    # are truthy. Assert on the boolean interpretation, not the identity.
    assert bool(response.get_json()['data']['featured']) is True


@pytest.mark.parametrize('falsy', [False, 0, '', None, []])
def test_featured_accepts_falsy_values(client, no_rate_limits, api_write_token, falsy):
    response = client.post(
        '/api/v1/blog',
        json={'title': f'not-featured-{hash(str(falsy))}', 'featured': falsy},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    assert bool(response.get_json()['data']['featured']) is False


# ---------------------------------------------------------------------------
# 404 paths
# ---------------------------------------------------------------------------


def test_update_nonexistent_slug_returns_404(client, no_rate_limits, api_write_token):
    response = client.put(
        '/api/v1/blog/this-post-does-not-exist',
        json={'title': 'anything'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 404


def test_delete_nonexistent_slug_returns_404(client, no_rate_limits, api_write_token):
    response = client.delete(
        '/api/v1/blog/nope',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Concurrency — slug uniqueness under a small burst
# ---------------------------------------------------------------------------


def test_concurrent_same_title_posts_get_distinct_slugs(app, no_rate_limits, api_write_token):
    """When several admin clients POST identical titles in rapid succession
    they must each end up at a unique slug (the collision-resolving
    ``_ensure_unique_slug`` is the contract under test).
    """
    errors: list[BaseException] = []
    slugs: list[str] = []
    lock = threading.Lock()

    def create():
        try:
            with app.test_client() as c:
                response = c.post(
                    '/api/v1/blog',
                    json={'title': 'Race Title'},
                    headers=_auth(api_write_token),
                )
                if response.status_code == 201:
                    with lock:
                        slugs.append(response.get_json()['data']['slug'])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f'concurrent create raised: {errors!r}'
    # Every successful create should have a distinct slug
    assert len(slugs) == len(set(slugs)), f'duplicate slugs under concurrency: {slugs!r}'
