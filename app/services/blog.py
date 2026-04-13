"""
Blog Service (app/services/blog.py)

Business logic for the blog_posts, blog_tags, and blog_post_tags tables.
Handles CRUD operations, slug generation, reading time calculation,
tag management, and publishing workflow.

Content is sanitized via nh3 before storage (same rules as content blocks).
Markdown posts store raw markdown and are rendered to HTML on display
via the mistune library.
"""

import math
import re

import mistune

from app.services.content import sanitize_html

# Markdown renderer (initialized once, reused across requests)
_markdown = mistune.create_markdown(escape=False)


def _slugify(text):
    """Convert a title into a URL-safe slug.

    Lowercases, replaces non-alphanumeric characters with hyphens,
    collapses consecutive hyphens, and strips leading/trailing hyphens.
    """
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _calculate_reading_time(content, content_format='html'):
    """Estimate reading time in minutes (words / 200, rounded up).

    Strips HTML tags before counting words so markup doesn't inflate
    the estimate.
    """
    if not content:
        return 0
    # Strip HTML tags for word counting
    text = re.sub(r'<[^>]+>', '', content)
    words = len(text.split())
    return max(1, math.ceil(words / 200))


def _ensure_unique_slug(db, slug, exclude_id=None):
    """Append a numeric suffix if the slug already exists.

    Returns a slug guaranteed to be unique in the blog_posts table.
    When editing an existing post (exclude_id set), the post's own
    slug is not considered a collision.
    """
    base_slug = slug
    counter = 1
    while True:
        query = 'SELECT id FROM blog_posts WHERE slug = ?'
        params = [slug]
        row = db.execute(query, params).fetchone()
        if row is None or (exclude_id and row['id'] == exclude_id):
            return slug
        counter += 1
        slug = f'{base_slug}-{counter}'


def render_post_content(post):
    """Render a post's content to HTML based on its content_format.

    Markdown posts are converted to HTML via mistune, then passed through
    sanitize_html() to strip any raw HTML the author embedded (mistune is
    configured with escape=False so <script>/event handlers would survive
    otherwise). HTML posts are returned as-is because they were sanitized
    when they were saved.
    """
    if post['content_format'] == 'markdown':
        return sanitize_html(_markdown(post['content'] or ''))
    return post['content'] or ''


# ============================================================
# READ OPERATIONS (public and admin)
# ============================================================


def get_published_posts(db, page=1, per_page=10):
    """Return published posts, newest first, with pagination.

    Returns:
        tuple: (list of post rows, total count for pagination)
    """
    total = db.execute(
        "SELECT COUNT(*) as cnt FROM blog_posts WHERE status = 'published'"
    ).fetchone()['cnt']

    offset = (page - 1) * per_page
    posts = db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' "
        'ORDER BY published_at DESC LIMIT ? OFFSET ?',
        (per_page, offset),
    ).fetchall()

    return posts, total


def get_post_by_slug(db, slug):
    """Return a single published post by slug, or None."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE slug = ? AND status = 'published'",
        (slug,),
    ).fetchone()


def get_post_by_id(db, post_id):
    """Return a post by ID (any status — for admin use)."""
    return db.execute('SELECT * FROM blog_posts WHERE id = ?', (post_id,)).fetchone()


def get_all_posts(db, status_filter=None):
    """Return all posts, optionally filtered by status (admin use).

    Args:
        db: Database connection.
        status_filter: None for all, or 'draft', 'published', 'archived'.
    """
    if status_filter:
        return db.execute(
            'SELECT * FROM blog_posts WHERE status = ? ORDER BY created_at DESC',
            (status_filter,),
        ).fetchall()
    return db.execute('SELECT * FROM blog_posts ORDER BY created_at DESC').fetchall()


def get_posts_by_tag(db, tag_slug, page=1, per_page=10):
    """Return published posts matching a tag slug, with pagination."""
    total = db.execute(
        'SELECT COUNT(*) as cnt FROM blog_posts bp '
        'JOIN blog_post_tags bpt ON bp.id = bpt.post_id '
        'JOIN blog_tags bt ON bt.id = bpt.tag_id '
        "WHERE bp.status = 'published' AND bt.slug = ?",
        (tag_slug,),
    ).fetchone()['cnt']

    offset = (page - 1) * per_page
    posts = db.execute(
        'SELECT bp.* FROM blog_posts bp '
        'JOIN blog_post_tags bpt ON bp.id = bpt.post_id '
        'JOIN blog_tags bt ON bt.id = bpt.tag_id '
        "WHERE bp.status = 'published' AND bt.slug = ? "
        'ORDER BY bp.published_at DESC LIMIT ? OFFSET ?',
        (tag_slug, per_page, offset),
    ).fetchall()

    return posts, total


def get_recent_posts(db, n=5):
    """Return the N most recent published posts."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' ORDER BY published_at DESC LIMIT ?",
        (n,),
    ).fetchall()


def get_featured_posts(db, n=3):
    """Return featured published posts for the landing page."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' AND featured = 1 "
        'ORDER BY published_at DESC LIMIT ?',
        (n,),
    ).fetchall()


# ============================================================
# TAG OPERATIONS
# ============================================================


def get_all_tags(db):
    """Return all tags ordered by name."""
    return db.execute('SELECT * FROM blog_tags ORDER BY name').fetchall()


def get_tags_for_post(db, post_id):
    """Return all tags attached to a specific post."""
    return db.execute(
        'SELECT bt.* FROM blog_tags bt '
        'JOIN blog_post_tags bpt ON bt.id = bpt.tag_id '
        'WHERE bpt.post_id = ? ORDER BY bt.name',
        (post_id,),
    ).fetchall()


def get_tag_by_slug(db, slug):
    """Return a tag by its slug, or None."""
    return db.execute('SELECT * FROM blog_tags WHERE slug = ?', (slug,)).fetchone()


def _sync_tags(db, post_id, tag_string):
    """Parse a comma-separated tag string and sync the junction table.

    Creates new tags as needed, removes old associations, and adds new ones.
    """
    # Parse tag names from the comma-separated input
    tag_names = [t.strip() for t in tag_string.split(',') if t.strip()]

    # Ensure each tag exists in blog_tags
    tag_ids = []
    for name in tag_names:
        slug = _slugify(name)
        if not slug:
            continue
        row = db.execute('SELECT id FROM blog_tags WHERE slug = ?', (slug,)).fetchone()
        if row:
            tag_ids.append(row['id'])
        else:
            cursor = db.execute(
                'INSERT INTO blog_tags (name, slug) VALUES (?, ?)',
                (name, slug),
            )
            tag_ids.append(cursor.lastrowid)

    # Replace all tag associations for this post
    db.execute('DELETE FROM blog_post_tags WHERE post_id = ?', (post_id,))
    for tag_id in tag_ids:
        db.execute(
            'INSERT OR IGNORE INTO blog_post_tags (post_id, tag_id) VALUES (?, ?)',
            (post_id, tag_id),
        )


# ============================================================
# WRITE OPERATIONS (admin)
# ============================================================


def create_post(
    db,
    title,
    summary='',
    content='',
    content_format='html',
    cover_image='',
    author='',
    tags='',
    meta_description='',
    featured=False,
):
    """Create a new blog post as a draft.

    Auto-generates a slug from the title and calculates reading time.
    Content is sanitized if HTML format.

    Returns:
        int: The new post's ID.
    """
    slug = _ensure_unique_slug(db, _slugify(title))
    if content_format == 'html':
        content = sanitize_html(content)
    reading_time = _calculate_reading_time(content, content_format)

    cursor = db.execute(
        'INSERT INTO blog_posts '
        '(slug, title, summary, content, content_format, cover_image, author, '
        'featured, reading_time, meta_description) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            slug,
            title.strip(),
            summary,
            content,
            content_format,
            cover_image,
            author,
            1 if featured else 0,
            reading_time,
            meta_description,
        ),
    )
    post_id = cursor.lastrowid

    if tags:
        _sync_tags(db, post_id, tags)

    db.commit()
    return post_id


def update_post(
    db,
    post_id,
    title,
    summary='',
    content='',
    content_format='html',
    cover_image='',
    author='',
    tags='',
    meta_description='',
    featured=False,
    slug=None,
):
    """Update an existing blog post.

    If slug is provided and different from auto-generated, uses the
    provided slug (after ensuring uniqueness). Recalculates reading time.
    """
    if slug:
        slug = _ensure_unique_slug(db, _slugify(slug), exclude_id=post_id)
    else:
        slug = _ensure_unique_slug(db, _slugify(title), exclude_id=post_id)

    if content_format == 'html':
        content = sanitize_html(content)
    reading_time = _calculate_reading_time(content, content_format)

    db.execute(
        'UPDATE blog_posts SET slug=?, title=?, summary=?, content=?, '
        'content_format=?, cover_image=?, author=?, featured=?, '
        'reading_time=?, meta_description=?, '
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (
            slug,
            title.strip(),
            summary,
            content,
            content_format,
            cover_image,
            author,
            1 if featured else 0,
            reading_time,
            meta_description,
            post_id,
        ),
    )

    _sync_tags(db, post_id, tags)
    db.commit()


def publish_post(db, post_id):
    """Set a post's status to 'published' and record the publish timestamp.

    If the post was previously published (has a published_at date), the
    original publish date is preserved.
    """
    post = get_post_by_id(db, post_id)
    if not post:
        return

    if post['published_at']:
        # Re-publishing — keep original date
        db.execute(
            "UPDATE blog_posts SET status='published', "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
            (post_id,),
        )
    else:
        db.execute(
            "UPDATE blog_posts SET status='published', "
            "published_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
            (post_id,),
        )
    db.commit()


def unpublish_post(db, post_id):
    """Revert a published post to draft status."""
    db.execute(
        "UPDATE blog_posts SET status='draft', "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (post_id,),
    )
    db.commit()


def archive_post(db, post_id):
    """Archive a post (removes from public view but preserves content)."""
    db.execute(
        "UPDATE blog_posts SET status='archived', "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (post_id,),
    )
    db.commit()


def delete_post(db, post_id):
    """Permanently delete a post and its tag associations."""
    db.execute('DELETE FROM blog_post_tags WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM blog_posts WHERE id = ?', (post_id,))
    db.commit()
