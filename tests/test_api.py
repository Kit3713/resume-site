"""
REST API Tests — Phase 16.1 + 16.2

Covers the public read-only endpoints at ``/api/v1/``:

* /site — site metadata + feature toggles
* /content/<slug> — single content block
* /services, /stats, /certifications — visible-only lists
* /portfolio — paginated photos with optional category filter
* /portfolio/<id> — single photo (hidden → 404)
* /portfolio/categories — distinct category list
* /testimonials — paginated approved reviews with optional tier filter

Infrastructure tested:
* JSON error envelope ``{error, code}`` on 404 / 405.
* ETag generation + ``If-None-Match`` 304 short-circuit.
* ``{data, pagination: {page, per_page, total, pages}}`` envelope.
* ``per_page`` is clamped to [1, 100]; malformed inputs fall back to
  the default.
* CSRF exemption — POSTs to any API path bypass CSRFProtect (since the
  routes exist as read-only 405s, not 400s).
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json(response):
    """Return the parsed JSON body of a response."""
    assert response.headers['Content-Type'].startswith('application/json'), (
        f'expected JSON, got {response.headers.get("Content-Type")!r}: {response.data[:200]!r}'
    )
    return json.loads(response.data.decode('utf-8'))


def _seed(app, sql, params=()):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute(sql, params)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# /api/v1/site
# ---------------------------------------------------------------------------


def test_site_metadata_returns_defaults(client):
    response = client.get('/api/v1/site')
    assert response.status_code == 200
    body = _json(response)
    assert body['api_version'] == 'v1'
    assert 'title' in body
    assert 'tagline' in body
    assert 'availability_status' in body
    assert body['available_locales'] == ['en']
    assert body['blog_enabled'] is False  # boolean, not the string 'false'


def test_site_metadata_reflects_settings_changes(client, app):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('site_title', 'Test Site')",
    )
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('blog_enabled', 'true')",
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    body = _json(client.get('/api/v1/site'))
    assert body['title'] == 'Test Site'
    assert body['blog_enabled'] is True


def test_site_metadata_sets_etag_header(client):
    response = client.get('/api/v1/site')
    assert response.headers.get('ETag', '').startswith('"')


def test_site_metadata_honours_if_none_match(client):
    first = client.get('/api/v1/site')
    etag = first.headers['ETag']
    cached = client.get('/api/v1/site', headers={'If-None-Match': etag})
    assert cached.status_code == 304
    assert cached.data == b''
    assert cached.headers['ETag'] == etag


def test_site_metadata_serves_fresh_on_etag_mismatch(client):
    response = client.get('/api/v1/site', headers={'If-None-Match': '"stale"'})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/v1/content/<slug>
# ---------------------------------------------------------------------------


def test_content_block_returns_row(client, app):
    _seed(
        app,
        'INSERT INTO content_blocks (slug, title, content, plain_text) VALUES (?, ?, ?, ?)',
        ('about', 'About Me', '<p>Hello</p>', 'Hello'),
    )
    body = _json(client.get('/api/v1/content/about'))
    assert body['data']['slug'] == 'about'
    assert body['data']['title'] == 'About Me'
    assert body['data']['content'] == '<p>Hello</p>'
    assert body['data']['plain_text'] == 'Hello'


def test_content_block_404_when_missing(client):
    response = client.get('/api/v1/content/nosuch')
    assert response.status_code == 404
    body = _json(response)
    assert body['code'] == 'NOT_FOUND'
    assert 'nosuch' in body['error']


# ---------------------------------------------------------------------------
# /api/v1/services and /api/v1/stats
# ---------------------------------------------------------------------------


def test_services_list_returns_visible_only(client, app):
    _seed(
        app,
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Web', 'Sites', '🌐', 1, 1)",
    )
    _seed(
        app,
        'INSERT INTO services (title, description, icon, sort_order, visible) '
        "VALUES ('Hidden', 'Private', '🙈', 2, 0)",
    )

    body = _json(client.get('/api/v1/services'))
    titles = [s['title'] for s in body['data']]
    assert titles == ['Web']


def test_services_empty_list(client):
    body = _json(client.get('/api/v1/services'))
    assert body['data'] == []


def test_stats_list_returns_visible_only(client, app):
    _seed(
        app,
        'INSERT INTO stats (label, value, suffix, sort_order, visible) '
        "VALUES ('Projects', 42, '+', 1, 1)",
    )
    _seed(
        app,
        'INSERT INTO stats (label, value, suffix, sort_order, visible) '
        "VALUES ('Draft', 0, '', 2, 0)",
    )

    body = _json(client.get('/api/v1/stats'))
    labels = [s['label'] for s in body['data']]
    assert labels == ['Projects']
    assert body['data'][0]['value'] == 42


# ---------------------------------------------------------------------------
# /api/v1/portfolio (list + pagination + filter)
# ---------------------------------------------------------------------------


def _seed_photos(app, rows):
    """Insert ``rows`` of (title, category, tier, sort_order) into photos."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    for idx, (title, category, tier, sort_order) in enumerate(rows):
        conn.execute(
            'INSERT INTO photos (filename, storage_name, title, category, '
            'display_tier, sort_order) VALUES (?, ?, ?, ?, ?, ?)',
            (f'{idx}.jpg', f'store_{idx}.jpg', title, category, tier, sort_order),
        )
    conn.commit()
    conn.close()


def test_portfolio_list_excludes_hidden(client, app):
    _seed_photos(
        app,
        [
            ('A', 'racks', 'featured', 1),
            ('B', 'racks', 'grid', 2),
            ('C', 'racks', 'hidden', 3),
        ],
    )
    body = _json(client.get('/api/v1/portfolio'))
    titles = [p['title'] for p in body['data']]
    assert titles == ['A', 'B']


def test_portfolio_list_filters_by_category(client, app):
    _seed_photos(
        app,
        [
            ('A', 'racks', 'grid', 1),
            ('B', 'panels', 'grid', 2),
            ('C', 'racks', 'grid', 3),
        ],
    )
    body = _json(client.get('/api/v1/portfolio?category=racks'))
    titles = [p['title'] for p in body['data']]
    assert sorted(titles) == ['A', 'C']
    assert body['pagination']['total'] == 2


def test_portfolio_pagination_envelope_and_math(client, app):
    _seed_photos(app, [(f'P{i}', 'c', 'grid', i) for i in range(25)])
    body = _json(client.get('/api/v1/portfolio?per_page=10&page=2'))

    assert body['pagination'] == {
        'page': 2,
        'per_page': 10,
        'total': 25,
        'pages': 3,
    }
    assert len(body['data']) == 10
    titles = [p['title'] for p in body['data']]
    assert titles == [f'P{i}' for i in range(10, 20)]


def test_portfolio_pagination_beyond_end_returns_empty(client, app):
    _seed_photos(app, [(f'P{i}', 'c', 'grid', i) for i in range(3)])
    body = _json(client.get('/api/v1/portfolio?page=9&per_page=10'))
    assert body['data'] == []
    assert body['pagination']['total'] == 3


def test_portfolio_per_page_is_clamped_to_max(client, app):
    _seed_photos(app, [(f'P{i}', 'c', 'grid', i) for i in range(5)])
    body = _json(client.get('/api/v1/portfolio?per_page=9999'))
    # per_page caps at 100; total is 5 → single page returned
    assert body['pagination']['per_page'] == 100


def test_portfolio_per_page_zero_falls_back_to_default(client, app):
    _seed_photos(app, [(f'P{i}', 'c', 'grid', i) for i in range(5)])
    body = _json(client.get('/api/v1/portfolio?per_page=0'))
    assert body['pagination']['per_page'] == 20


def test_portfolio_page_zero_and_negatives_clamp_to_one(client, app):
    _seed_photos(app, [(f'P{i}', 'c', 'grid', i) for i in range(3)])
    for raw in ('0', '-5', 'banana'):
        body = _json(client.get(f'/api/v1/portfolio?page={raw}'))
        assert body['pagination']['page'] == 1


# ---------------------------------------------------------------------------
# /api/v1/portfolio/<id>
# ---------------------------------------------------------------------------


def test_portfolio_detail_returns_photo(client, app):
    _seed_photos(app, [('One', 'racks', 'featured', 1)])
    body = _json(client.get('/api/v1/portfolio/1'))
    assert body['data']['id'] == 1
    assert body['data']['title'] == 'One'


def test_portfolio_detail_404_for_hidden(client, app):
    _seed_photos(app, [('Secret', 'racks', 'hidden', 1)])
    response = client.get('/api/v1/portfolio/1')
    assert response.status_code == 404


def test_portfolio_detail_404_for_missing(client):
    response = client.get('/api/v1/portfolio/9999')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_portfolio_categories_distinct_only(client, app):
    _seed_photos(
        app,
        [
            ('A', 'racks', 'grid', 1),
            ('B', 'racks', 'grid', 2),
            ('C', 'panels', 'grid', 3),
            ('D', '', 'grid', 4),  # empty category excluded
        ],
    )
    body = _json(client.get('/api/v1/portfolio/categories'))
    assert sorted(body['data']) == ['panels', 'racks']


# ---------------------------------------------------------------------------
# /api/v1/testimonials
# ---------------------------------------------------------------------------


def _seed_reviews(app, rows):
    """Insert approved reviews. rows: list of (name, message, tier)."""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    for name, message, tier in rows:
        conn.execute(
            'INSERT INTO reviews (reviewer_name, reviewer_title, message, '
            'type, status, display_tier) VALUES (?, ?, ?, ?, ?, ?)',
            (name, 'Title', message, 'recommendation', 'approved', tier),
        )
    conn.commit()
    conn.close()


def test_testimonials_returns_approved_reviews(client, app):
    _seed_reviews(
        app,
        [
            ('Alice', 'Great', 'featured'),
            ('Bob', 'Good', 'standard'),
        ],
    )
    body = _json(client.get('/api/v1/testimonials'))
    names = [r['reviewer_name'] for r in body['data']]
    assert sorted(names) == ['Alice', 'Bob']
    assert body['pagination']['total'] == 2


def test_testimonials_filter_by_tier(client, app):
    _seed_reviews(
        app,
        [
            ('Alice', 'Great', 'featured'),
            ('Bob', 'Good', 'standard'),
        ],
    )
    body = _json(client.get('/api/v1/testimonials?tier=featured'))
    assert [r['reviewer_name'] for r in body['data']] == ['Alice']


def test_testimonials_pagination(client, app):
    _seed_reviews(
        app,
        [(f'Name{i}', f'msg{i}', 'standard') for i in range(15)],
    )
    body = _json(client.get('/api/v1/testimonials?per_page=5&page=2'))
    assert body['pagination'] == {
        'page': 2,
        'per_page': 5,
        'total': 15,
        'pages': 3,
    }
    assert len(body['data']) == 5


def test_testimonials_ignores_pending(client, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        'INSERT INTO reviews (reviewer_name, message, status, display_tier, type) '
        "VALUES ('Pending', 'x', 'pending', 'standard', 'recommendation')"
    )
    conn.commit()
    conn.close()

    body = _json(client.get('/api/v1/testimonials'))
    assert body['data'] == []


# ---------------------------------------------------------------------------
# /api/v1/certifications
# ---------------------------------------------------------------------------


def test_certifications_returns_visible_only(client, app):
    _seed(
        app,
        'INSERT INTO certifications (name, issuer, visible, sort_order) '
        "VALUES ('A+', 'CompTIA', 1, 1)",
    )
    _seed(
        app,
        'INSERT INTO certifications (name, issuer, visible, sort_order) '
        "VALUES ('Hidden', 'Secret', 0, 2)",
    )

    body = _json(client.get('/api/v1/certifications'))
    names = [c['name'] for c in body['data']]
    assert names == ['A+']


# ---------------------------------------------------------------------------
# Error envelope + method handling
# ---------------------------------------------------------------------------


def test_unknown_path_returns_json_404(client):
    """A 404 under /api/v1/ returns the uniform JSON envelope, not HTML."""
    response = client.get('/api/v1/does-not-exist')
    assert response.status_code == 404
    body = _json(response)
    assert body['code'] == 'NOT_FOUND'


def test_post_to_read_endpoint_returns_json_405(client):
    response = client.post('/api/v1/services')
    assert response.status_code == 405
    body = _json(response)
    assert body['code'] == 'METHOD_NOT_ALLOWED'


def test_csrf_does_not_apply_to_api(csrf_client):
    """With CSRFProtect enabled globally, API POSTs bypass it.

    We verify indirectly: a POST to a read endpoint should return 405
    (method not allowed) rather than 400 (CSRF rejection). A 400 here
    would mean the API blueprint is not exempt from CSRFProtect.
    """
    response = csrf_client.post('/api/v1/services')
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# /api/v1/case-studies/<slug>
# ---------------------------------------------------------------------------


def test_case_study_detail_returns_published(client, app):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('case_studies_enabled', 'true')",
    )
    _seed(
        app,
        'INSERT INTO case_studies (slug, title, summary, problem, solution, result, published) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        ('rack-build', 'Rack Build', 'Summary', 'P', 'S', 'R', 1),
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    body = _json(client.get('/api/v1/case-studies/rack-build'))
    assert body['data']['slug'] == 'rack-build'
    assert body['data']['title'] == 'Rack Build'
    assert body['data']['problem'] == 'P'


def test_case_study_detail_404_when_feature_disabled(client, app):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('case_studies_enabled', 'false')",
    )
    _seed(
        app,
        'INSERT INTO case_studies (slug, title, published) VALUES (?, ?, ?)',
        ('exists', 'Exists', 1),
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    response = client.get('/api/v1/case-studies/exists')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_case_study_detail_404_when_unpublished(client, app):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('case_studies_enabled', 'true')",
    )
    _seed(
        app,
        'INSERT INTO case_studies (slug, title, published) VALUES (?, ?, ?)',
        ('draft', 'Draft', 0),
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    response = client.get('/api/v1/case-studies/draft')
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/v1/projects (+ /<slug>)
# ---------------------------------------------------------------------------


def _seed_project(app, slug, title, *, visible=1, has_detail_page=1):
    _seed(
        app,
        'INSERT INTO projects (slug, title, summary, description, github_url, '
        'has_detail_page, sort_order, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (
            slug,
            title,
            'Summary',
            'Description',
            'https://github.com/x/y',
            has_detail_page,
            1,
            visible,
        ),
    )


def test_projects_list_visible_only(client, app):
    _seed_project(app, 'ironclad', 'Ironclad')
    _seed_project(app, 'hidden', 'Hidden', visible=0)

    body = _json(client.get('/api/v1/projects'))
    slugs = [p['slug'] for p in body['data']]
    assert slugs == ['ironclad']


def test_project_detail_returns_row(client, app):
    _seed_project(app, 'ironclad', 'Ironclad')
    body = _json(client.get('/api/v1/projects/ironclad'))
    assert body['data']['slug'] == 'ironclad'
    assert body['data']['title'] == 'Ironclad'
    assert body['data']['has_detail_page'] == 1


def test_project_detail_404_without_detail_page(client, app):
    _seed_project(app, 'github-only', 'Github Only', has_detail_page=0)
    # List endpoint still surfaces it (has_detail_page is only a detail-route gate).
    body = _json(client.get('/api/v1/projects'))
    assert [p['slug'] for p in body['data']] == ['github-only']
    # But the detail endpoint 404s — nothing to show.
    response = client.get('/api/v1/projects/github-only')
    assert response.status_code == 404


def test_project_detail_404_when_hidden(client, app):
    _seed_project(app, 'secret', 'Secret', visible=0)
    response = client.get('/api/v1/projects/secret')
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/v1/blog  (+ /<slug>, /tags)
# ---------------------------------------------------------------------------


def _enable_blog(app, enabled=True):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('blog_enabled', ?)",
        ('true' if enabled else 'false',),
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def _seed_blog_post(
    app, slug, title, *, status='published', featured=0, published_at='2026-01-01T00:00:00Z'
):
    _seed(
        app,
        'INSERT INTO blog_posts (slug, title, summary, content, author, status, '
        'featured, reading_time, meta_description, published_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (slug, title, 'Sum', '<p>Body</p>', 'Author', status, featured, 3, 'meta', published_at),
    )


def _tag_post(app, post_slug, *tags):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute('PRAGMA foreign_keys=ON')
    post_id = conn.execute('SELECT id FROM blog_posts WHERE slug = ?', (post_slug,)).fetchone()[0]
    for name in tags:
        slug = name.lower().replace(' ', '-')
        conn.execute(
            'INSERT OR IGNORE INTO blog_tags (name, slug) VALUES (?, ?)',
            (name, slug),
        )
        tag_id = conn.execute('SELECT id FROM blog_tags WHERE slug = ?', (slug,)).fetchone()[0]
        conn.execute(
            'INSERT OR IGNORE INTO blog_post_tags (post_id, tag_id) VALUES (?, ?)',
            (post_id, tag_id),
        )
    conn.commit()
    conn.close()


def test_blog_list_404_when_disabled(client, app):
    _enable_blog(app, False)
    response = client.get('/api/v1/blog')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_blog_list_returns_published_posts(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'first', 'First Post', published_at='2026-01-01T00:00:00Z')
    _seed_blog_post(app, 'second', 'Second Post', published_at='2026-02-01T00:00:00Z')
    _seed_blog_post(app, 'draft', 'Draft', status='draft')

    body = _json(client.get('/api/v1/blog'))
    slugs = [p['slug'] for p in body['data']]
    # Newest first; draft excluded.
    assert slugs == ['second', 'first']
    assert body['pagination']['total'] == 2


def test_blog_list_filters_by_tag(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'a', 'A', published_at='2026-01-01T00:00:00Z')
    _seed_blog_post(app, 'b', 'B', published_at='2026-02-01T00:00:00Z')
    _tag_post(app, 'a', 'Homelab')
    _tag_post(app, 'b', 'Networking')

    body = _json(client.get('/api/v1/blog?tag=homelab'))
    slugs = [p['slug'] for p in body['data']]
    assert slugs == ['a']
    assert body['pagination']['total'] == 1


def test_blog_list_includes_tags_on_each_post(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'tagged', 'Tagged')
    _tag_post(app, 'tagged', 'Homelab', 'Networking')

    body = _json(client.get('/api/v1/blog'))
    post = body['data'][0]
    tag_slugs = sorted(t['slug'] for t in post['tags'])
    assert tag_slugs == ['homelab', 'networking']


def test_blog_detail_returns_rendered_html(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'hello', 'Hello')
    _tag_post(app, 'hello', 'Welcome')

    body = _json(client.get('/api/v1/blog/hello'))
    assert body['data']['slug'] == 'hello'
    assert body['data']['content'] == '<p>Body</p>'
    # HTML posts are passed through as-is by render_post_content.
    assert body['data']['rendered_html'] == '<p>Body</p>'
    assert [t['slug'] for t in body['data']['tags']] == ['welcome']


def test_blog_detail_404_for_draft(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'sneak', 'Sneak', status='draft')
    response = client.get('/api/v1/blog/sneak')
    assert response.status_code == 404


def test_blog_detail_404_when_disabled(client, app):
    _enable_blog(app, False)
    response = client.get('/api/v1/blog/anything')
    assert response.status_code == 404


def test_blog_tags_returns_counts(client, app):
    _enable_blog(app)
    _seed_blog_post(app, 'a', 'A')
    _seed_blog_post(app, 'b', 'B')
    _seed_blog_post(app, 'c', 'C', status='draft')
    _tag_post(app, 'a', 'Homelab')
    _tag_post(app, 'b', 'Homelab')
    _tag_post(app, 'c', 'Homelab')  # draft shouldn't count

    body = _json(client.get('/api/v1/blog/tags'))
    tags_by_slug = {t['slug']: t for t in body['data']}
    assert 'homelab' in tags_by_slug
    assert tags_by_slug['homelab']['post_count'] == 2


def test_blog_tags_route_resolves_before_slug(client, app):
    """Flask should prefer the static '/blog/tags' over '/blog/<slug>'.

    Regression guard: ensure an unlucky post slug of 'tags' doesn't
    shadow the tags endpoint.
    """
    _enable_blog(app)
    _seed_blog_post(app, 'tags', 'A post literally slugged tags')

    response = client.get('/api/v1/blog/tags')
    body = _json(response)
    # A slug-detail response would carry 'data' as an object with slug key.
    # A tags-list response has 'data' as a list of tag dicts.
    assert isinstance(body['data'], list)


def test_blog_tags_404_when_disabled(client, app):
    _enable_blog(app, False)
    response = client.get('/api/v1/blog/tags')
    assert response.status_code == 404


# ===========================================================================
# WRITE ENDPOINTS (Phase 16.3)
# ===========================================================================


@pytest.fixture
def no_rate_limits(app):
    """Disable Flask-Limiter for tests that exercise write endpoints.

    The limiter's in-memory storage persists across the test process
    (storage_uri='memory://'), so rapid-fire POSTs in test bodies can
    hit the configured cap. This fixture turns the limiter off; tests
    that specifically want to exercise rate limiting skip it.
    """
    app.config['RATELIMIT_ENABLED'] = False
    yield
    app.config['RATELIMIT_ENABLED'] = True


@pytest.fixture
def api_write_token(app):
    """Create a ``write``-scoped API token and return the raw Bearer value."""
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        generated = generate_token(get_db(), name='test-bot', scope='read,write')
    return generated.raw


def _auth(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


# ---------------------------------------------------------------------------
# JSON Content-Type enforcement
# ---------------------------------------------------------------------------


def test_post_without_json_content_type_returns_415(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        data='title=Hello',
        headers={
            'Authorization': f'Bearer {api_write_token}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    assert response.status_code == 415
    body = _json(response)
    assert body['code'] == 'UNSUPPORTED_MEDIA_TYPE'
    assert body['details']['received'] == 'application/x-www-form-urlencoded'


def test_post_without_any_content_type_returns_415(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 415


def test_get_without_content_type_is_fine(client):
    """The middleware only gates POST/PUT/PATCH — GETs with no body must pass."""
    assert client.get('/api/v1/site').status_code == 200


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_blog_create_without_token_returns_401(client, no_rate_limits):
    response = client.post(
        '/api/v1/blog',
        json={'title': 'Hello'},
    )
    assert response.status_code == 401
    assert response.headers.get('WWW-Authenticate') == 'Bearer'


def test_blog_create_with_read_only_token_returns_403(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        raw = generate_token(get_db(), name='read-only', scope='read').raw

    response = client.post(
        '/api/v1/blog',
        json={'title': 'Hello'},
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert response.status_code == 403
    assert _json(response)['error'] == 'insufficient_scope'


def test_blog_create_with_revoked_token_returns_401(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token, revoke_token

    with app.app_context():
        token = generate_token(get_db(), name='gone', scope='write')
        revoke_token(get_db(), token.id)

    response = client.post(
        '/api/v1/blog',
        json={'title': 'Hello'},
        headers={'Authorization': f'Bearer {token.raw}'},
    )
    assert response.status_code == 401
    assert _json(response)['error'] == 'revoked'


# ---------------------------------------------------------------------------
# POST /api/v1/blog
# ---------------------------------------------------------------------------


def test_blog_create_draft_by_default(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={
            'title': 'Hello World',
            'content': '<p>First post</p>',
            'author': 'Admin',
            'tags': 'hello, world',
        },
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    body = _json(response)
    assert body['data']['title'] == 'Hello World'
    assert body['data']['slug'] == 'hello-world'
    assert body['data']['status'] != 'published'  # default draft
    tag_slugs = sorted(t['slug'] for t in body['data']['tags'])
    assert tag_slugs == ['hello', 'world']


def test_blog_create_with_publish_flag_publishes(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={'title': 'Live Post', 'content': '<p>Now live</p>', 'publish': True},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 201
    body = _json(response)
    assert body['data']['status'] == 'published'
    assert body['data']['published_at']


def test_blog_create_requires_title(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={'content': 'no title'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400
    body = _json(response)
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['details']['field'] == 'title'


def test_blog_create_rejects_whitespace_only_title(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog',
        json={'title': '   '},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


def test_blog_create_emits_event(client, no_rate_limits, api_write_token):
    from app.events import Events, clear, register

    captured = []
    clear()
    register(Events.BLOG_PUBLISHED, lambda **p: captured.append(('published', p)))
    register(Events.BLOG_UPDATED, lambda **p: captured.append(('updated', p)))
    try:
        client.post(
            '/api/v1/blog',
            json={'title': 'With Event', 'publish': True},
            headers=_auth(api_write_token),
        )
    finally:
        clear()
    assert len(captured) == 1
    kind, payload = captured[0]
    assert kind == 'published'
    assert payload['slug'] == 'with-event'
    assert payload['source'] == 'api.blog_create'


# ---------------------------------------------------------------------------
# PUT /api/v1/blog/<slug>
# ---------------------------------------------------------------------------


def test_blog_update_changes_title_and_content(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'original', 'Original')

    response = client.put(
        '/api/v1/blog/original',
        json={'title': 'Renamed', 'content': '<p>New body</p>'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['title'] == 'Renamed'
    assert body['data']['content'] == '<p>New body</p>'


def test_blog_update_404_for_unknown_slug(client, no_rate_limits, api_write_token):
    response = client.put(
        '/api/v1/blog/ghost',
        json={'title': 'Ghost'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 404


def test_blog_update_preserves_untouched_fields(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'original', 'Original')

    # Update only the summary; title + content must be preserved.
    response = client.put(
        '/api/v1/blog/original',
        json={'summary': 'Brand new summary'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['title'] == 'Original'
    assert body['data']['summary'] == 'Brand new summary'


def test_blog_update_rejects_empty_title(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'original', 'Original')

    response = client.put(
        '/api/v1/blog/original',
        json={'title': '   '},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/v1/blog/<slug>
# ---------------------------------------------------------------------------


def test_blog_delete_returns_204(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'doomed', 'Doomed')

    response = client.delete(
        '/api/v1/blog/doomed',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 204
    assert response.data == b''

    # Row actually gone.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT id FROM blog_posts WHERE slug = ?', ('doomed',)).fetchone()
    conn.close()
    assert row is None


def test_blog_delete_404_for_missing(client, no_rate_limits, api_write_token):
    response = client.delete(
        '/api/v1/blog/ghost',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# publish / unpublish
# ---------------------------------------------------------------------------


def test_blog_publish_changes_status(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'draft', 'Draft Post', status='draft', published_at=None)

    response = client.post(
        '/api/v1/blog/draft/publish',
        json={},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['status'] == 'published'
    assert body['data']['published_at']


def test_blog_publish_emits_event(client, no_rate_limits, api_write_token, app):
    from app.events import Events, clear, register

    _enable_blog(app)
    _seed_blog_post(app, 'd', 'D', status='draft', published_at=None)
    captured = []
    clear()
    register(Events.BLOG_PUBLISHED, lambda **p: captured.append(p))
    try:
        client.post(
            '/api/v1/blog/d/publish',
            json={},
            headers=_auth(api_write_token),
        )
    finally:
        clear()
    assert len(captured) == 1
    assert captured[0]['slug'] == 'd'
    assert captured[0]['source'] == 'api.blog_publish'


def test_blog_unpublish_reverts_to_draft(client, no_rate_limits, api_write_token, app):
    _enable_blog(app)
    _seed_blog_post(app, 'live', 'Live')

    response = client.post(
        '/api/v1/blog/live/unpublish',
        json={},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    assert _json(response)['data']['status'] == 'draft'


def test_blog_publish_404_for_unknown(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/blog/ghost/publish',
        json={},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/contact (public — no token required)
# ---------------------------------------------------------------------------


def test_contact_accepts_valid_submission(client, no_rate_limits, app):
    response = client.post(
        '/api/v1/contact',
        json={
            'name': 'Ada Lovelace',
            'email': 'ada@example.com',
            'message': 'Hello world',
        },
    )
    assert response.status_code == 201
    body = _json(response)
    assert body['data']['ok'] is True
    assert body['data']['is_spam'] is False

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT name, email, is_spam FROM contact_submissions').fetchone()
    conn.close()
    assert row['name'] == 'Ada Lovelace'
    assert row['email'] == 'ada@example.com'
    assert row['is_spam'] == 0


def test_contact_honeypot_flags_spam(client, no_rate_limits, app):
    response = client.post(
        '/api/v1/contact',
        json={
            'name': 'Bot',
            'email': 'bot@spam.com',
            'message': 'buy viagra',
            'website': 'http://spam.example',
        },
    )
    assert response.status_code == 201
    assert _json(response)['data']['is_spam'] is True

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT is_spam FROM contact_submissions').fetchone()
    conn.close()
    assert row[0] == 1


def test_contact_requires_name_email_message(client, no_rate_limits):
    response = client.post(
        '/api/v1/contact',
        json={'name': '', 'email': '', 'message': ''},
    )
    assert response.status_code == 400
    body = _json(response)
    assert body['code'] == 'VALIDATION_ERROR'
    assert set(body['details']['fields']) == {'name', 'email', 'message'}


def test_contact_rejects_malformed_email(client, no_rate_limits):
    response = client.post(
        '/api/v1/contact',
        json={
            'name': 'Test',
            'email': 'not-an-email',
            'message': 'Hello',
        },
    )
    assert response.status_code == 400
    assert _json(response)['details']['field'] == 'email'


def test_contact_404_when_disabled(client, no_rate_limits, app):
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('contact_form_enabled', 'false')",
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    response = client.post(
        '/api/v1/contact',
        json={'name': 'A', 'email': 'a@b.co', 'message': 'x'},
    )
    assert response.status_code == 404


def test_contact_emits_event(client, no_rate_limits, app):
    from app.events import Events, clear, register

    captured = []
    clear()
    register(Events.CONTACT_SUBMITTED, lambda **p: captured.append(p))
    try:
        client.post(
            '/api/v1/contact',
            json={'name': 'E', 'email': 'e@x.co', 'message': 'hi'},
        )
    finally:
        clear()
    assert len(captured) == 1
    assert captured[0]['is_spam'] is False
    assert captured[0]['source'] == 'api.contact_submit'
    _ = app


def test_contact_per_ip_cap_returns_429(client, no_rate_limits, app):
    """After 5 non-spam submissions from an IP in the past hour, return 429.

    Phase 24.2 (#60) — ip_address is a salted SHA-256 of the client IP,
    not the raw address. Seed rows with the same hash the route will
    compute so the rate-limit count matches.
    """
    from app.services.logging import hash_client_ip

    ip_hash = hash_client_ip('127.0.0.1', app.secret_key)

    # Seed 5 prior submissions from 127.0.0.1 (the test client IP).
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    for i in range(5):
        conn.execute(
            'INSERT INTO contact_submissions (name, email, message, ip_address, '
            'user_agent, is_spam) VALUES (?, ?, ?, ?, ?, ?)',
            (f'User{i}', f'u{i}@x.co', 'msg', ip_hash, 'other', 0),
        )
    conn.commit()
    conn.close()

    response = client.post(
        '/api/v1/contact',
        json={'name': 'Sixth', 'email': 's@x.co', 'message': 'over the cap'},
    )
    assert response.status_code == 429
    body = _json(response)
    assert body['code'] == 'RATE_LIMITED'
    assert body['details']['retry_after_minutes'] == 60


# ===========================================================================
# PORTFOLIO WRITE ENDPOINTS (Phase 16.3b)
# ===========================================================================


def _png_bytes(*, width=100, height=100, color=(255, 0, 0)):
    """Build a valid PNG image in memory using Pillow.

    Used by the upload tests — feeding raw bytes avoids shipping a
    fixture file in the repo while exercising the real Pillow pipeline
    (magic-byte check, EXIF stripping, re-encode).
    """
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new('RGB', (width, height), color=color).save(buf, 'PNG')
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# POST /api/v1/portfolio
# ---------------------------------------------------------------------------


def test_portfolio_upload_happy_path(client, no_rate_limits, api_write_token, app):
    response = client.post(
        '/api/v1/portfolio',
        data={
            'photo': (_png_bytes(), 'test.png'),
            'title': 'Rack Build',
            'description': 'Bottom-up rack cabling',
            'category': 'racks',
            'display_tier': 'featured',
        },
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 201, response.data
    body = _json(response)
    assert body['data']['title'] == 'Rack Build'
    assert body['data']['category'] == 'racks'
    assert body['data']['display_tier'] == 'featured'

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT filename, storage_name, width, height FROM photos').fetchone()
    conn.close()
    assert row['filename'] == 'test.png'
    assert row['storage_name'].endswith('.png')
    # Pillow returned real dimensions after processing.
    assert row['width'] == 100
    assert row['height'] == 100


def test_portfolio_upload_requires_write_scope(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        raw = generate_token(get_db(), name='read-only', scope='read').raw

    response = client.post(
        '/api/v1/portfolio',
        data={'photo': (_png_bytes(), 'test.png')},
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert response.status_code == 403


def test_portfolio_upload_without_token_returns_401(client, no_rate_limits):
    response = client.post(
        '/api/v1/portfolio',
        data={'photo': (_png_bytes(), 'test.png')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 401


def test_portfolio_upload_missing_file_returns_400(client, no_rate_limits, api_write_token):
    response = client.post(
        '/api/v1/portfolio',
        data={'title': 'No file'},
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 400
    body = _json(response)
    assert body['code'] == 'VALIDATION_ERROR'
    assert body['details']['field'] == 'photo'


def test_portfolio_upload_rejects_invalid_extension(client, no_rate_limits, api_write_token):
    from io import BytesIO

    response = client.post(
        '/api/v1/portfolio',
        data={'photo': (BytesIO(b'not-an-image'), 'resume.txt')},
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 400
    assert _json(response)['details']['reason'] == 'invalid_type'


def test_portfolio_upload_rejects_magic_byte_mismatch(client, no_rate_limits, api_write_token):
    """A .png extension with non-PNG content must be rejected by the
    magic-byte check in process_upload."""
    from io import BytesIO

    response = client.post(
        '/api/v1/portfolio',
        data={'photo': (BytesIO(b'\x00\x00\x00 fake'), 'fake.png')},
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 400
    assert _json(response)['details']['reason'] == 'rejected'


def test_portfolio_upload_rejects_bad_display_tier(client, no_rate_limits, api_write_token, app):
    response = client.post(
        '/api/v1/portfolio',
        data={
            'photo': (_png_bytes(), 'ok.png'),
            'display_tier': 'super-featured',
        },
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 400
    body = _json(response)
    assert body['details']['field'] == 'display_tier'
    assert 'grid' in body['details']['allowed']

    # The uploaded file should have been cleaned up — no orphan on disk.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT COUNT(*) FROM photos').fetchone()
    conn.close()
    assert row[0] == 0


def test_portfolio_upload_emits_event(client, no_rate_limits, api_write_token):
    from app.events import Events, clear, register

    captured = []
    clear()
    register(Events.PHOTO_UPLOADED, lambda **p: captured.append(p))
    try:
        client.post(
            '/api/v1/portfolio',
            data={
                'photo': (_png_bytes(), 'event.png'),
                'title': 'Event Photo',
                'display_tier': 'grid',
            },
            content_type='multipart/form-data',
            headers={'Authorization': f'Bearer {api_write_token}'},
        )
    finally:
        clear()
    assert len(captured) == 1
    payload = captured[0]
    assert payload['title'] == 'Event Photo'
    assert payload['display_tier'] == 'grid'
    assert payload['source'] == 'api.portfolio_create'
    assert 'photo_id' in payload
    assert 'storage_name' in payload
    assert 'file_size' in payload


# ---------------------------------------------------------------------------
# PUT /api/v1/portfolio/<id>
# ---------------------------------------------------------------------------


def test_portfolio_update_metadata(client, no_rate_limits, api_write_token, app):
    _seed_photos(app, [('Old', 'racks', 'grid', 1)])

    response = client.put(
        '/api/v1/portfolio/1',
        json={
            'title': 'New Title',
            'description': 'New description',
            'display_tier': 'featured',
        },
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['title'] == 'New Title'
    assert body['data']['display_tier'] == 'featured'


def test_portfolio_update_partial_preserves_fields(client, no_rate_limits, api_write_token, app):
    _seed_photos(app, [('Original', 'racks', 'grid', 1)])
    response = client.put(
        '/api/v1/portfolio/1',
        json={'description': 'added a description'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['title'] == 'Original'  # unchanged
    assert body['data']['description'] == 'added a description'


def test_portfolio_update_404_for_missing(client, no_rate_limits, api_write_token):
    response = client.put(
        '/api/v1/portfolio/999',
        json={'title': 'x'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 404


def test_portfolio_update_rejects_bad_tier(client, no_rate_limits, api_write_token, app):
    _seed_photos(app, [('Ok', 'c', 'grid', 1)])
    response = client.put(
        '/api/v1/portfolio/1',
        json={'display_tier': 'bogus'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400


def test_portfolio_update_rejects_non_int_sort_order(client, no_rate_limits, api_write_token, app):
    _seed_photos(app, [('Ok', 'c', 'grid', 1)])
    response = client.put(
        '/api/v1/portfolio/1',
        json={'sort_order': 'first'},
        headers=_auth(api_write_token),
    )
    assert response.status_code == 400
    assert _json(response)['details']['field'] == 'sort_order'


def test_portfolio_update_requires_write_scope(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    _seed_photos(app, [('Ok', 'c', 'grid', 1)])
    with app.app_context():
        raw = generate_token(get_db(), name='r', scope='read').raw

    response = client.put(
        '/api/v1/portfolio/1',
        json={'title': 'nope'},
        headers={'Authorization': f'Bearer {raw}', 'Content-Type': 'application/json'},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/portfolio/<id>
# ---------------------------------------------------------------------------


def test_portfolio_delete_removes_row_and_file(client, no_rate_limits, api_write_token, app):
    """End-to-end: upload a photo, delete it, confirm row + file both gone."""
    upload = client.post(
        '/api/v1/portfolio',
        data={'photo': (_png_bytes(), 'doomed.png'), 'title': 'Doomed'},
        content_type='multipart/form-data',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert upload.status_code == 201
    photo_id = _json(upload)['data']['id']

    # Grab storage_name so we can verify the file is gone.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    storage_name = conn.execute(
        'SELECT storage_name FROM photos WHERE id = ?', (photo_id,)
    ).fetchone()['storage_name']
    conn.close()

    response = client.delete(
        f'/api/v1/portfolio/{photo_id}',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 204

    # Row gone.
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT COUNT(*) FROM photos WHERE id = ?', (photo_id,)).fetchone()
    conn.close()
    assert row[0] == 0

    # File gone.
    import os

    file_path = os.path.join(app.config['PHOTO_STORAGE'], storage_name)
    assert not os.path.exists(file_path)


def test_portfolio_delete_404_for_missing(client, no_rate_limits, api_write_token):
    response = client.delete(
        '/api/v1/portfolio/999',
        headers={'Authorization': f'Bearer {api_write_token}'},
    )
    assert response.status_code == 404


def test_portfolio_delete_requires_write_scope(client, no_rate_limits, app):
    from app.db import get_db
    from app.services.api_tokens import generate_token

    _seed_photos(app, [('Ok', 'c', 'grid', 1)])
    with app.app_context():
        raw = generate_token(get_db(), name='r', scope='read').raw

    response = client.delete(
        '/api/v1/portfolio/1',
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert response.status_code == 403


# ===========================================================================
# ADMIN ENDPOINTS (Phase 16.4)
# ===========================================================================


@pytest.fixture
def api_admin_token(app):
    """Create an ``admin``-scoped API token and return the raw value."""
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        generated = generate_token(get_db(), name='admin-bot', scope='admin')
    return generated.raw


def _admin_auth(token, *, with_json=False):
    headers = {'Authorization': f'Bearer {token}'}
    if with_json:
        headers['Content-Type'] = 'application/json'
    return headers


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_admin_endpoint_rejects_write_scope(client, no_rate_limits, app):
    """A token with write scope (but not admin) must 403 on /admin routes."""
    from app.db import get_db
    from app.services.api_tokens import generate_token

    with app.app_context():
        raw = generate_token(get_db(), name='write-only', scope='write').raw

    response = client.get(
        '/api/v1/admin/settings',
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert response.status_code == 403


def test_admin_endpoint_rejects_missing_token(client, no_rate_limits):
    response = client.get('/api/v1/admin/settings')
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /admin/settings
# ---------------------------------------------------------------------------


def test_admin_settings_list_returns_grouped_and_flat(client, no_rate_limits, api_admin_token):
    response = client.get(
        '/api/v1/admin/settings',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert 'categories' in body['data']
    assert 'flat' in body['data']
    assert all('name' in c and 'settings' in c for c in body['data']['categories'])
    assert 'site_title' in body['data']['flat']


# ---------------------------------------------------------------------------
# PUT /admin/settings
# ---------------------------------------------------------------------------


def test_admin_settings_update_applies_changes(client, no_rate_limits, api_admin_token, app):
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': 'API Updated', 'blog_enabled': True},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    body = _json(response)
    assert 'site_title' in body['data']['updated_keys']
    assert body['data']['flat']['site_title'] == 'API Updated'
    assert body['data']['flat']['blog_enabled'] == 'true'


def test_admin_settings_update_ignores_unknown_keys(client, no_rate_limits, api_admin_token):
    """Unknown keys are silently dropped — matches HTML form contract."""
    response = client.put(
        '/api/v1/admin/settings',
        json={'site_title': 'Ok', 'inject_malicious': 'x'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    assert 'inject_malicious' not in _json(response)['data']['updated_keys']


def test_admin_settings_update_emits_event(client, no_rate_limits, api_admin_token):
    from app.events import Events, clear, register

    captured = []
    clear()
    register(Events.SETTINGS_CHANGED, lambda **p: captured.append(p))
    try:
        client.put(
            '/api/v1/admin/settings',
            json={'site_title': 'Event Test'},
            headers=_admin_auth(api_admin_token, with_json=True),
        )
    finally:
        clear()
    assert len(captured) == 1
    assert 'site_title' in captured[0]['keys']


# ---------------------------------------------------------------------------
# GET /admin/analytics
# ---------------------------------------------------------------------------


def test_admin_analytics_returns_summary(client, no_rate_limits, api_admin_token, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    for path in ('/', '/', '/blog', '/portfolio'):
        conn.execute(
            'INSERT INTO page_views (path, referrer, user_agent, ip_address) VALUES (?, ?, ?, ?)',
            (path, '', 'pytest', '127.0.0.1'),
        )
    conn.commit()
    conn.close()

    response = client.get(
        '/api/v1/admin/analytics',
        headers=_admin_auth(api_admin_function := api_admin_token),
    )
    assert response.status_code == 200
    body = _json(response)['data']
    # The analytics middleware may add rows for the API request itself,
    # so assert >= our seed count rather than an exact match.
    assert body['total_views'] >= 4
    assert body['popular_pages'][0]['path'] == '/'
    assert body['popular_pages'][0]['count'] == 2
    assert body['window_days'] == 7
    assert isinstance(body['time_series'], list)
    _ = api_admin_function


def test_admin_analytics_respects_days_clamp(client, no_rate_limits, api_admin_token):
    response = client.get(
        '/api/v1/admin/analytics?days=999',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 200
    assert _json(response)['data']['window_days'] == 90


# ---------------------------------------------------------------------------
# GET /admin/activity
# ---------------------------------------------------------------------------


def test_admin_activity_returns_recent_entries(client, no_rate_limits, api_admin_token, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        'INSERT INTO admin_activity_log (action, category, detail, admin_user) '
        "VALUES ('Test action', 'test', 'detail', 'admin')"
    )
    conn.commit()
    conn.close()

    response = client.get(
        '/api/v1/admin/activity',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 200
    body = _json(response)
    assert isinstance(body['data'], list)
    assert any(e['action'] == 'Test action' for e in body['data'])


# ---------------------------------------------------------------------------
# GET /admin/reviews
# ---------------------------------------------------------------------------


def _seed_review(app, name, status='pending', tier='standard'):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        'INSERT INTO reviews (reviewer_name, reviewer_title, message, type, '
        'status, display_tier) VALUES (?, ?, ?, ?, ?, ?)',
        (name, 'Title', 'msg', 'recommendation', status, tier),
    )
    conn.commit()
    conn.close()


def test_admin_reviews_list_all(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='pending')
    _seed_review(app, 'B', status='approved')
    _seed_review(app, 'C', status='rejected')

    response = client.get(
        '/api/v1/admin/reviews',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 200
    names = sorted(r['reviewer_name'] for r in _json(response)['data'])
    assert names == ['A', 'B', 'C']


def test_admin_reviews_filter_by_status(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='pending')
    _seed_review(app, 'B', status='approved')

    response = client.get(
        '/api/v1/admin/reviews?status=approved',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 200
    names = [r['reviewer_name'] for r in _json(response)['data']]
    assert names == ['B']


def test_admin_reviews_rejects_invalid_status(client, no_rate_limits, api_admin_token):
    response = client.get(
        '/api/v1/admin/reviews?status=nonsense',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 400
    assert _json(response)['code'] == 'VALIDATION_ERROR'


# ---------------------------------------------------------------------------
# PUT /admin/reviews/<id>
# ---------------------------------------------------------------------------


def test_admin_review_approve(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='pending')
    response = client.put(
        '/api/v1/admin/reviews/1',
        json={'action': 'approve', 'display_tier': 'featured'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    body = _json(response)
    assert body['data']['status'] == 'approved'
    assert body['data']['display_tier'] == 'featured'


def test_admin_review_reject(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='pending')
    response = client.put(
        '/api/v1/admin/reviews/1',
        json={'action': 'reject'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    assert _json(response)['data']['status'] == 'rejected'


def test_admin_review_set_tier(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='approved', tier='standard')
    response = client.put(
        '/api/v1/admin/reviews/1',
        json={'action': 'set_tier', 'display_tier': 'featured'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 200
    assert _json(response)['data']['display_tier'] == 'featured'


def test_admin_review_404_for_missing(client, no_rate_limits, api_admin_token):
    response = client.put(
        '/api/v1/admin/reviews/999',
        json={'action': 'approve'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 404


def test_admin_review_rejects_bad_action(client, no_rate_limits, api_admin_token, app):
    _seed_review(app, 'A', status='pending')
    response = client.put(
        '/api/v1/admin/reviews/1',
        json={'action': 'nuke'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 400


def test_admin_review_approve_emits_event(client, no_rate_limits, api_admin_token, app):
    from app.events import Events, clear, register

    _seed_review(app, 'A', status='pending')
    captured = []
    clear()
    register(Events.REVIEW_APPROVED, lambda **p: captured.append(p))
    try:
        client.put(
            '/api/v1/admin/reviews/1',
            json={'action': 'approve', 'display_tier': 'featured'},
            headers=_admin_auth(api_admin_token, with_json=True),
        )
    finally:
        clear()
    assert len(captured) == 1
    assert captured[0]['review_id'] == 1
    assert captured[0]['display_tier'] == 'featured'


# ---------------------------------------------------------------------------
# POST /admin/tokens + DELETE /admin/tokens/<id>
# ---------------------------------------------------------------------------


def test_admin_review_token_create(client, no_rate_limits, api_admin_token, app):
    response = client.post(
        '/api/v1/admin/tokens',
        json={'name': 'Ada Lovelace', 'type': 'recommendation'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 201
    body = _json(response)
    assert body['data']['name'] == 'Ada Lovelace'
    assert body['data']['type'] == 'recommendation'
    assert body['data']['token']

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT COUNT(*) FROM review_tokens').fetchone()
    conn.close()
    assert row[0] == 1


def test_admin_review_token_create_rejects_bad_type(client, no_rate_limits, api_admin_token):
    response = client.post(
        '/api/v1/admin/tokens',
        json={'name': 'X', 'type': 'admin'},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 400
    assert _json(response)['details']['field'] == 'type'


def test_admin_review_token_delete(client, no_rate_limits, api_admin_token, app):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        "INSERT INTO review_tokens (token, name, type) VALUES ('t1', 'Ada', 'recommendation')"
    )
    conn.commit()
    tok_id = conn.execute('SELECT id FROM review_tokens').fetchone()[0]
    conn.close()

    response = client.delete(
        f'/api/v1/admin/tokens/{tok_id}',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 204

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    row = conn.execute('SELECT COUNT(*) FROM review_tokens').fetchone()
    conn.close()
    assert row[0] == 0


def test_admin_review_token_delete_404_for_missing(client, no_rate_limits, api_admin_token):
    response = client.delete(
        '/api/v1/admin/tokens/999',
        headers=_admin_auth(api_admin_token),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/contacts
# ---------------------------------------------------------------------------


def _seed_contact(app, name, *, is_spam=0):
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute(
        'INSERT INTO contact_submissions (name, email, message, is_spam) VALUES (?, ?, ?, ?)',
        (name, f'{name.lower()}@x.co', f'msg from {name}', is_spam),
    )
    conn.commit()
    conn.close()


def test_admin_contacts_list_excludes_spam_by_default(client, no_rate_limits, api_admin_token, app):
    _seed_contact(app, 'Ada')
    _seed_contact(app, 'Bob', is_spam=1)
    body = _json(
        client.get(
            '/api/v1/admin/contacts',
            headers=_admin_auth(api_admin_token),
        )
    )
    names = [c['name'] for c in body['data']]
    assert names == ['Ada']
    assert body['pagination']['total'] == 1


def test_admin_contacts_include_spam_param(client, no_rate_limits, api_admin_token, app):
    _seed_contact(app, 'Ada')
    _seed_contact(app, 'Bob', is_spam=1)
    body = _json(
        client.get(
            '/api/v1/admin/contacts?include_spam=true',
            headers=_admin_auth(api_admin_token),
        )
    )
    assert body['pagination']['total'] == 2


def test_admin_contacts_pagination(client, no_rate_limits, api_admin_token, app):
    for i in range(15):
        _seed_contact(app, f'User{i}')
    body = _json(
        client.get(
            '/api/v1/admin/contacts?per_page=5&page=2',
            headers=_admin_auth(api_admin_token),
        )
    )
    assert body['pagination'] == {'page': 2, 'per_page': 5, 'total': 15, 'pages': 3}
    assert len(body['data']) == 5


# ---------------------------------------------------------------------------
# POST /admin/backup
# ---------------------------------------------------------------------------


def test_admin_backup_creates_archive(
    client, no_rate_limits, api_admin_token, tmp_path, monkeypatch
):
    import os

    backup_dir = tmp_path / 'api-backups'
    monkeypatch.setenv('RESUME_SITE_BACKUP_DIR', str(backup_dir))

    response = client.post(
        '/api/v1/admin/backup',
        json={'db_only': True},
        headers=_admin_auth(api_admin_token, with_json=True),
    )
    assert response.status_code == 201
    body = _json(response)
    assert body['data']['archive_name'].endswith('.tar.gz')
    assert body['data']['size_bytes'] > 0
    assert body['data']['db_only'] is True
    assert os.path.isfile(body['data']['archive_path'])


def test_admin_backup_emits_event(client, no_rate_limits, api_admin_token, tmp_path, monkeypatch):
    from app.events import Events, clear, register

    monkeypatch.setenv('RESUME_SITE_BACKUP_DIR', str(tmp_path / 'evt-backups'))

    captured = []
    clear()
    register(Events.BACKUP_COMPLETED, lambda **p: captured.append(p))
    try:
        response = client.post(
            '/api/v1/admin/backup',
            json={'db_only': True},
            headers=_admin_auth(api_admin_token, with_json=True),
        )
    finally:
        clear()
    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0]['db_only'] is True


# ---------------------------------------------------------------------------
# csrf_client fixture (mirrors tests/test_security.py for this module)
# ---------------------------------------------------------------------------


@pytest.fixture
def csrf_client(tmp_path):
    from app import create_app
    from tests.test_security import _init_test_db, _write_test_config

    config_path = _write_test_config(tmp_path)
    flask_app = create_app(config_path=config_path)
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_SECRET_KEY'] = 'csrf-test-secret'
    _init_test_db(str(tmp_path / 'test.db'))
    return flask_app.test_client()


# ===========================================================================
# Phase 16.5 — OpenAPI 3.0 Specification + Swagger UI
# ===========================================================================
#
# Three new routes (`/api/v1/openapi.yaml`, `/api/v1/openapi.json`,
# `/api/v1/docs`) sit behind the ``api_docs_enabled`` setting (default
# ``false``). When the flag is off, every route 404s with the standard
# error envelope so a probe can't tell the endpoints exist.


def _enable_api_docs(app, enabled=True):
    """Flip api_docs_enabled and bust the settings cache.

    Mirrors ``_enable_blog`` above. The settings cache TTL is 30 s, so
    the explicit ``invalidate_cache()`` is necessary for tests to see
    the change immediately.
    """
    _seed(
        app,
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('api_docs_enabled', ?)",
        ('true' if enabled else 'false',),
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def test_openapi_yaml_404_when_flag_off_by_default(client):
    response = client.get('/api/v1/openapi.yaml')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_openapi_json_404_when_flag_off_by_default(client):
    response = client.get('/api/v1/openapi.json')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_swagger_ui_404_when_flag_off_by_default(client):
    response = client.get('/api/v1/docs')
    assert response.status_code == 404
    assert _json(response)['code'] == 'NOT_FOUND'


def test_openapi_yaml_returns_yaml_when_enabled(client, app):
    _enable_api_docs(app)
    response = client.get('/api/v1/openapi.yaml')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('application/yaml')
    body = response.data.decode('utf-8')
    # Sanity-check the spec content rather than over-asserting.
    assert body.startswith('openapi:')
    assert '/site' in body
    # ETag must be present so clients can cache.
    assert response.headers['ETag'].startswith('"')


def test_openapi_yaml_etag_roundtrip(client, app):
    _enable_api_docs(app)
    first = client.get('/api/v1/openapi.yaml')
    assert first.status_code == 200
    etag = first.headers['ETag']

    second = client.get('/api/v1/openapi.yaml', headers={'If-None-Match': etag})
    assert second.status_code == 304
    assert second.headers['ETag'] == etag
    # 304 must not carry a body.
    assert second.data == b''


def test_openapi_json_parses_and_matches_yaml(client, app):
    _enable_api_docs(app)
    response = client.get('/api/v1/openapi.json')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('application/json')
    parsed = json.loads(response.data)
    assert parsed['openapi'].startswith('3.0')
    assert 'paths' in parsed
    # Should match the YAML version's path set 1:1 (no transformation
    # other than YAML→JSON serialisation).
    yaml_resp = client.get('/api/v1/openapi.yaml')
    yaml = pytest.importorskip('yaml')
    yaml_parsed = yaml.safe_load(yaml_resp.data)
    assert sorted(parsed['paths'].keys()) == sorted(yaml_parsed['paths'].keys())


def test_swagger_ui_renders_when_enabled(client, app):
    _enable_api_docs(app)
    response = client.get('/api/v1/docs')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('text/html')
    body = response.data.decode('utf-8')
    # Standalone template structure.
    assert '<div id="swagger-ui"></div>' in body
    # Pinned CDN version (catches accidental upgrades).
    assert 'swagger-ui-dist@5.17.14' in body
    # Init script loaded as an external file — no inline script body.
    assert '/static/js/swagger-init.js' in body


def test_swagger_ui_template_has_no_inline_script_bodies(client, app):
    """CSP forward-compat: no `<script>...</script>` with an inline body.

    Once Phase 13.2 promotes CSP from report-only to enforced, any
    inline script body would break the docs page. Catching it now keeps
    the upgrade path clear.
    """
    _enable_api_docs(app)
    response = client.get('/api/v1/docs')
    body = response.data.decode('utf-8')
    # Find every `<script ...>...</script>` and assert the content
    # between `>` and `</script>` is whitespace-only.
    pattern = re.compile(r'<script\b[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
    for inline in pattern.findall(body):
        assert not inline.strip(), (
            f'docs.html has an inline script body — move it to /static/js/: {inline[:120]!r}'
        )


# ============================================================
# Phase 16.6 — API Test Expansion
# ============================================================


def test_expired_token_returns_401(client, no_rate_limits, app):
    """An expired API token should be rejected with 401."""
    from app.services.api_tokens import generate_token

    with app.app_context():
        from app.db import get_db

        token = generate_token(
            get_db(),
            name='expired-bot',
            scope='read,write',
            expires_at='2020-01-01T00:00:00Z',
        )

    response = client.post(
        '/api/v1/blog',
        json={'title': 'test'},
        headers={'Authorization': f'Bearer {token.raw}'},
    )
    assert response.status_code == 401
    data = response.get_json()
    assert data['error'] == 'expired'


def test_content_negotiation_defaults_to_json(client):
    """Requests without an Accept header should still get JSON."""
    response = client.get('/api/v1/site')
    assert response.status_code == 200
    assert response.content_type.startswith('application/json')
    data = response.get_json()
    assert data is not None


def test_content_negotiation_accepts_wildcard(client):
    """Accept: */* should return JSON."""
    response = client.get('/api/v1/site', headers={'Accept': '*/*'})
    assert response.status_code == 200
    assert response.content_type.startswith('application/json')


def test_etag_304_roundtrip_on_services(client):
    """Second identical request with If-None-Match should return 304."""
    first = client.get('/api/v1/services')
    assert first.status_code == 200
    etag = first.headers.get('ETag')
    assert etag

    second = client.get('/api/v1/services', headers={'If-None-Match': etag})
    assert second.status_code == 304


def test_etag_stale_returns_200(client):
    """A mismatched ETag should return fresh 200."""
    response = client.get('/api/v1/services', headers={'If-None-Match': '"stale-hash"'})
    assert response.status_code == 200


def test_pagination_per_page_clamped_to_max(client, app):
    """per_page above 100 should be clamped to 100."""
    response = client.get('/api/v1/portfolio?per_page=999')
    assert response.status_code == 200
    data = response.get_json()
    assert data['pagination']['per_page'] <= 100


def test_pagination_per_page_clamped_to_min(client):
    """per_page below 1 should be clamped to 1."""
    response = client.get('/api/v1/portfolio?per_page=0')
    assert response.status_code == 200
    data = response.get_json()
    assert data['pagination']['per_page'] >= 1
