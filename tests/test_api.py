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
    assert 'server_time' in body


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
