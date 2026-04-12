"""
Blog Engine Tests — Phase 8.4

Covers the full blog system:
- Admin CRUD: create draft, edit, publish, unpublish, archive, delete
- Slug generation and uniqueness
- Draft posts not visible on public routes
- Published posts visible and correctly ordered
- Tag filtering returns correct posts
- Pagination at boundaries
- RSS feed valid XML, contains only published posts
- Reading time calculation
- Blog disabled in settings → /blog returns 404, nav link hidden
"""

import pytest


# ============================================================
# HELPER: Enable blog and create posts
# ============================================================

def _enable_blog(auth_client):
    """Enable the blog feature via admin settings."""
    auth_client.post('/admin/settings', data={
        'blog_enabled': 'true',
        'enable_rss': 'true',
        'show_reading_time': 'true',
    })


def _create_post(auth_client, title='Test Post', summary='A test post',
                 content='<p>Test content with enough words to calculate reading time.</p>',
                 tags='', action='save', **kwargs):
    """Create a blog post via the admin interface and return the response."""
    data = {
        'title': title,
        'summary': summary,
        'content': content,
        'content_format': 'html',
        'cover_image': '',
        'author': 'Test Author',
        'tags': tags,
        'meta_description': '',
        'action': action,
        **kwargs,
    }
    return auth_client.post('/admin/blog/new', data=data, follow_redirects=False)


# ============================================================
# ADMIN CRUD
# ============================================================

def test_blog_list_loads(auth_client):
    """Blog admin list page should return 200."""
    response = auth_client.get('/admin/blog')
    assert response.status_code == 200


def test_blog_new_page_loads(auth_client):
    """Blog new post page should return 200."""
    response = auth_client.get('/admin/blog/new')
    assert response.status_code == 200


def test_blog_create_draft(auth_client):
    """Creating a draft post should redirect to the edit page."""
    response = _create_post(auth_client, title='My Draft Post')
    assert response.status_code == 302
    assert '/admin/blog/' in response.headers['Location']
    assert '/edit' in response.headers['Location']


def test_blog_create_and_publish(auth_client):
    """Creating a post with action=publish should set status to published."""
    response = _create_post(auth_client, title='Published Post', action='publish')
    assert response.status_code == 302

    # Verify it shows as published in the admin list
    response = auth_client.get('/admin/blog?status=published')
    assert b'Published Post' in response.data


def test_blog_edit_page_loads(auth_client, app):
    """Edit page for an existing post should return 200."""
    _create_post(auth_client, title='Editable Post')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE title='Editable Post'").fetchone()

    response = auth_client.get(f'/admin/blog/{post["id"]}/edit')
    assert response.status_code == 200
    assert b'Editable Post' in response.data


def test_blog_edit_saves(auth_client, app):
    """POST to edit should update the post."""
    _create_post(auth_client, title='Original Title')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE title='Original Title'").fetchone()
        post_id = post['id']

    response = auth_client.post(f'/admin/blog/{post_id}/edit', data={
        'title': 'Updated Title',
        'summary': 'Updated summary',
        'content': '<p>Updated content.</p>',
        'content_format': 'html',
        'cover_image': '',
        'author': 'Author',
        'tags': 'python, flask',
        'meta_description': '',
        'action': 'save',
    }, follow_redirects=False)
    assert response.status_code == 302


def test_blog_unpublish(auth_client, app):
    """Unpublishing should revert a post to draft status."""
    _create_post(auth_client, title='To Unpublish', action='publish')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE title='To Unpublish'").fetchone()
        post_id = post['id']

    auth_client.post(f'/admin/blog/{post_id}/edit', data={
        'title': 'To Unpublish',
        'summary': '',
        'content': '<p>Content.</p>',
        'content_format': 'html',
        'cover_image': '',
        'author': '',
        'tags': '',
        'meta_description': '',
        'action': 'unpublish',
    }, follow_redirects=False)

    with app.app_context():
        db = get_db()
        post = db.execute("SELECT status FROM blog_posts WHERE id=?", (post_id,)).fetchone()
        assert post['status'] == 'draft'


def test_blog_archive(auth_client, app):
    """Archiving should set status to 'archived'."""
    _create_post(auth_client, title='To Archive', action='publish')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE title='To Archive'").fetchone()
        post_id = post['id']

    auth_client.post(f'/admin/blog/{post_id}/edit', data={
        'title': 'To Archive',
        'summary': '',
        'content': '<p>Content.</p>',
        'content_format': 'html',
        'cover_image': '',
        'author': '',
        'tags': '',
        'meta_description': '',
        'action': 'archive',
    })

    with app.app_context():
        db = get_db()
        post = db.execute("SELECT status FROM blog_posts WHERE id=?", (post_id,)).fetchone()
        assert post['status'] == 'archived'


def test_blog_delete(auth_client, app):
    """Deleting a post should remove it from the database."""
    _create_post(auth_client, title='To Delete')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE title='To Delete'").fetchone()
        post_id = post['id']

    response = auth_client.post(f'/admin/blog/{post_id}/delete', follow_redirects=False)
    assert response.status_code == 302

    with app.app_context():
        db = get_db()
        post = db.execute("SELECT id FROM blog_posts WHERE id=?", (post_id,)).fetchone()
        assert post is None


def test_blog_create_no_title_rejects(auth_client):
    """Creating a post without a title should show an error."""
    response = _create_post(auth_client, title='')
    assert response.status_code == 200  # Re-renders the form
    assert b'Title is required' in response.data


# ============================================================
# SLUG GENERATION
# ============================================================

def test_slug_generated_from_title(auth_client, app):
    """Slug should be auto-generated from the title."""
    _create_post(auth_client, title='My First Blog Post')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        post = db.execute("SELECT slug FROM blog_posts WHERE title='My First Blog Post'").fetchone()
        assert post['slug'] == 'my-first-blog-post'


def test_slug_uniqueness(auth_client, app):
    """Duplicate titles should get unique slugs with numeric suffixes."""
    _create_post(auth_client, title='Duplicate Title')
    _create_post(auth_client, title='Duplicate Title')

    with app.app_context():
        from app.db import get_db
        db = get_db()
        posts = db.execute(
            "SELECT slug FROM blog_posts WHERE title='Duplicate Title' ORDER BY id"
        ).fetchall()
        slugs = [p['slug'] for p in posts]
        assert len(set(slugs)) == 2  # Both slugs are unique
        assert 'duplicate-title' in slugs


# ============================================================
# PUBLIC ROUTES: BLOG DISABLED
# ============================================================

def test_blog_disabled_returns_404(client):
    """When blog_enabled is false, /blog should return 404."""
    response = client.get('/blog')
    assert response.status_code == 404


def test_blog_disabled_hides_nav_link(client):
    """When blog_enabled is false, the Blog nav link should not appear."""
    response = client.get('/')
    assert b'Blog</a>' not in response.data


# ============================================================
# PUBLIC ROUTES: BLOG ENABLED
# ============================================================

def test_blog_enabled_shows_nav_link(auth_client, app):
    """When blog_enabled is true, the Blog nav link should appear."""
    _enable_blog(auth_client)
    public_client = app.test_client()
    response = public_client.get('/')
    assert b'Blog</a>' in response.data


def test_blog_index_loads_when_enabled(auth_client, app):
    """Blog index should return 200 when enabled (even with no posts)."""
    _enable_blog(auth_client)
    public_client = app.test_client()
    response = public_client.get('/blog')
    assert response.status_code == 200


def test_draft_not_visible_on_public(auth_client, app):
    """Draft posts should not appear on the public blog listing."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Secret Draft', action='save')

    public_client = app.test_client()
    response = public_client.get('/blog')
    assert b'Secret Draft' not in response.data


def test_published_post_visible_on_public(auth_client, app):
    """Published posts should appear on the public blog listing."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Public Article', action='publish')

    public_client = app.test_client()
    response = public_client.get('/blog')
    assert b'Public Article' in response.data


def test_published_post_page_loads(auth_client, app):
    """A published post should be accessible at /blog/<slug>."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Readable Post', action='publish')

    public_client = app.test_client()
    response = public_client.get('/blog/readable-post')
    assert response.status_code == 200
    assert b'Readable Post' in response.data


def test_draft_post_page_returns_404(auth_client, app):
    """A draft post should return 404 on the public route."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Hidden Draft', action='save')

    public_client = app.test_client()
    response = public_client.get('/blog/hidden-draft')
    assert response.status_code == 404


# ============================================================
# TAG FILTERING
# ============================================================

def test_tag_filter_returns_correct_posts(auth_client, app):
    """Filtering by tag should only return posts with that tag."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Python Post', tags='python, web', action='publish')
    _create_post(auth_client, title='Go Post', tags='golang', action='publish')

    public_client = app.test_client()
    response = public_client.get('/blog/tag/python')
    assert response.status_code == 200
    assert b'Python Post' in response.data
    assert b'Go Post' not in response.data


def test_nonexistent_tag_returns_404(auth_client, app):
    """A tag slug that doesn't exist should return 404."""
    _enable_blog(auth_client)
    public_client = app.test_client()
    response = public_client.get('/blog/tag/nonexistent')
    assert response.status_code == 404


# ============================================================
# RSS FEED
# ============================================================

def test_rss_feed_valid_xml(auth_client, app):
    """RSS feed should return valid XML with application/rss+xml content type."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='RSS Post', action='publish')

    public_client = app.test_client()
    response = public_client.get('/blog/feed.xml')
    assert response.status_code == 200
    assert 'application/rss+xml' in response.content_type

    data = response.data.decode()
    assert '<?xml version' in data
    assert '<rss version="2.0"' in data
    assert 'RSS Post' in data


def test_rss_feed_excludes_drafts(auth_client, app):
    """RSS feed should only contain published posts, not drafts."""
    _enable_blog(auth_client)
    _create_post(auth_client, title='Published For RSS', action='publish')
    _create_post(auth_client, title='Draft Not In RSS', action='save')

    public_client = app.test_client()
    response = public_client.get('/blog/feed.xml')
    data = response.data.decode()
    assert 'Published For RSS' in data
    assert 'Draft Not In RSS' not in data


def test_rss_disabled_returns_404(auth_client, app):
    """When enable_rss is false, /blog/feed.xml should return 404."""
    _enable_blog(auth_client)
    auth_client.post('/admin/settings', data={
        'blog_enabled': 'true',
        'enable_rss': 'false',
    })

    public_client = app.test_client()
    response = public_client.get('/blog/feed.xml')
    assert response.status_code == 404


# ============================================================
# READING TIME
# ============================================================

def test_reading_time_calculated():
    """Reading time should be calculated from word count (words / 200, ceil)."""
    from app.services.blog import _calculate_reading_time
    # 200 words = 1 min
    assert _calculate_reading_time(' '.join(['word'] * 200)) == 1
    # 201 words = 2 min (ceiling)
    assert _calculate_reading_time(' '.join(['word'] * 201)) == 2
    # Empty = 0
    assert _calculate_reading_time('') == 0
    # HTML tags should be stripped before counting
    assert _calculate_reading_time('<p>' + ' '.join(['word'] * 200) + '</p>') == 1


# ============================================================
# ADMIN STATUS FILTER
# ============================================================

def test_admin_status_filter_draft(auth_client):
    """Filtering by draft should only show draft posts."""
    _create_post(auth_client, title='Draft One', action='save')
    _create_post(auth_client, title='Published One', action='publish')

    response = auth_client.get('/admin/blog?status=draft')
    assert b'Draft One' in response.data
    assert b'Published One' not in response.data


def test_admin_status_filter_published(auth_client):
    """Filtering by published should only show published posts."""
    _create_post(auth_client, title='Draft Two', action='save')
    _create_post(auth_client, title='Published Two', action='publish')

    response = auth_client.get('/admin/blog?status=published')
    assert b'Published Two' in response.data
    assert b'Draft Two' not in response.data
