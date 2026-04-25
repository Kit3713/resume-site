"""
Blog Service (app/services/blog.py)

Business logic for the blog_posts, blog_tags, and blog_post_tags tables.
Handles CRUD operations, slug generation, reading time calculation,
tag management, and publishing workflow.

Content is sanitized via nh3 before storage (same rules as content blocks).
Markdown posts store raw markdown and are rendered to HTML on display
via the mistune library.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable

import mistune

from app.services.content import sanitize_html
from app.services.pagination import offset_for
from app.services.text import slugify

# Markdown renderer (initialized once, reused across requests)
_markdown = mistune.create_markdown(escape=False)


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


def render_post_content(post: sqlite3.Row) -> str:
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


def get_published_posts(
    db: sqlite3.Connection, page: int = 1, per_page: int = 10
) -> tuple[list[sqlite3.Row], int]:
    """Return published posts, newest first, with pagination.

    Returns:
        tuple: (list of post rows, total count for pagination)
    """
    total = db.execute(
        "SELECT COUNT(*) as cnt FROM blog_posts WHERE status = 'published'"
    ).fetchone()['cnt']

    posts = db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' "
        'ORDER BY published_at DESC LIMIT ? OFFSET ?',
        (per_page, offset_for(page, per_page)),
    ).fetchall()

    return posts, total


def get_post_by_slug(db: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    """Return a single published post by slug, or None."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE slug = ? AND status = 'published'",
        (slug,),
    ).fetchone()


def get_post_by_id(db: sqlite3.Connection, post_id: int) -> sqlite3.Row | None:
    """Return a post by ID (any status — for admin use)."""
    return db.execute('SELECT * FROM blog_posts WHERE id = ?', (post_id,)).fetchone()


def get_all_posts(db: sqlite3.Connection, status_filter: str | None = None) -> list[sqlite3.Row]:
    """Return all posts, optionally filtered by status (admin use).

    Kept for callers that don't need pagination. For the admin list
    route use :func:`get_all_posts_paginated` — at 150+ posts the
    unpaginated path scales linearly (documented 8.3 ms baseline).

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


def get_all_posts_paginated(
    db: sqlite3.Connection,
    status_filter: str | None = None,
    *,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[sqlite3.Row], int]:
    """Return a paginated admin post listing + the total row count.

    Phase 26.3 (#54) — the admin listing used to render every row in
    one shot. At 150 posts the documented baseline was 8.3 ms and
    scaling linearly. Pagination caps the query at ``per_page`` rows
    per request.

    Args:
        db: Database connection.
        status_filter: None for all, or 'draft', 'published', 'archived'.
        page: 1-indexed page number. Values < 1 are clamped to 1.
        per_page: Rows per page. Clamped to the inclusive range [1, 100].

    Returns:
        Tuple of (rows, total_count). ``total_count`` reflects the
        filtered population so the paginator can build the page list.
    """
    page = max(int(page or 1), 1)
    per_page = max(1, min(int(per_page or 25), 100))
    offset = (page - 1) * per_page

    if status_filter:
        total = db.execute(
            'SELECT COUNT(*) AS cnt FROM blog_posts WHERE status = ?',
            (status_filter,),
        ).fetchone()['cnt']
        rows = db.execute(
            'SELECT * FROM blog_posts WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (status_filter, per_page, offset),
        ).fetchall()
    else:
        total = db.execute('SELECT COUNT(*) AS cnt FROM blog_posts').fetchone()['cnt']
        rows = db.execute(
            'SELECT * FROM blog_posts ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (per_page, offset),
        ).fetchall()
    return rows, total


def get_posts_by_tag(
    db: sqlite3.Connection, tag_slug: str, page: int = 1, per_page: int = 10
) -> tuple[list[sqlite3.Row], int]:
    """Return published posts matching a tag slug, with pagination."""
    total = db.execute(
        'SELECT COUNT(*) as cnt FROM blog_posts bp '
        'JOIN blog_post_tags bpt ON bp.id = bpt.post_id '
        'JOIN blog_tags bt ON bt.id = bpt.tag_id '
        "WHERE bp.status = 'published' AND bt.slug = ?",
        (tag_slug,),
    ).fetchone()['cnt']

    posts = db.execute(
        'SELECT bp.* FROM blog_posts bp '
        'JOIN blog_post_tags bpt ON bp.id = bpt.post_id '
        'JOIN blog_tags bt ON bt.id = bpt.tag_id '
        "WHERE bp.status = 'published' AND bt.slug = ? "
        'ORDER BY bp.published_at DESC LIMIT ? OFFSET ?',
        (tag_slug, per_page, offset_for(page, per_page)),
    ).fetchall()

    return posts, total


def get_recent_posts(db: sqlite3.Connection, n: int = 5) -> list[sqlite3.Row]:
    """Return the N most recent published posts."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' ORDER BY published_at DESC LIMIT ?",
        (n,),
    ).fetchall()


def get_featured_posts(db: sqlite3.Connection, n: int = 3) -> list[sqlite3.Row]:
    """Return featured published posts for the landing page."""
    return db.execute(
        "SELECT * FROM blog_posts WHERE status = 'published' AND featured = 1 "
        'ORDER BY published_at DESC LIMIT ?',
        (n,),
    ).fetchall()


# ============================================================
# TAG OPERATIONS
# ============================================================


def get_all_tags(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all tags ordered by name."""
    return db.execute('SELECT * FROM blog_tags ORDER BY name').fetchall()


def get_tags_for_post(db: sqlite3.Connection, post_id: int) -> list[sqlite3.Row]:
    """Return all tags attached to a specific post."""
    return db.execute(
        'SELECT bt.* FROM blog_tags bt '
        'JOIN blog_post_tags bpt ON bt.id = bpt.tag_id '
        'WHERE bpt.post_id = ? ORDER BY bt.name',
        (post_id,),
    ).fetchall()


def get_tags_for_posts(
    db: sqlite3.Connection, post_ids: Iterable[int]
) -> dict[int, list[sqlite3.Row]]:
    """Return {post_id: [tag rows]} for a batch of posts in one query.

    Replaces the per-post call to `get_tags_for_post` on listing pages
    (Phase 12.1 N+1 elimination). Posts with no tags are present in the
    returned dict mapped to an empty list.

    Empty input returns an empty dict (avoids a no-rows query).
    """
    if not post_ids:
        return {}
    # `placeholders` is a string of `?` chars — no caller-supplied values are
    # interpolated into the SQL. Values still bind through db.execute params,
    # so this is not a SQL-injection vector.
    placeholders = ','.join(['?'] * len(post_ids))
    sql = (
        'SELECT bpt.post_id AS _post_id, bt.* FROM blog_tags bt '  # noqa: S608  # nosec B608
        'JOIN blog_post_tags bpt ON bt.id = bpt.tag_id '
        f'WHERE bpt.post_id IN ({placeholders}) ORDER BY bt.name'
    )
    rows = db.execute(sql, list(post_ids)).fetchall()
    result: dict[int, list] = {pid: [] for pid in post_ids}
    for row in rows:
        result[row['_post_id']].append(row)
    return result


def get_tag_by_slug(db: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
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
        slug = slugify(name)
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


# Cap on retry attempts after an IntegrityError on the slug. A pathological
# burst could in theory loop indefinitely; in normal operation one retry is
# sufficient because the racing INSERT has already taken `<slug>` so the
# next ``_ensure_unique_slug`` computes ``<slug>-2``.
_MAX_SLUG_RETRIES = 3


def create_post(
    db: sqlite3.Connection,
    title: str,
    summary: str = '',
    content: str = '',
    content_format: str = 'html',
    cover_image: str = '',
    author: str = '',
    tags: str = '',
    meta_description: str = '',
    featured: bool = False,
) -> int | None:
    """Create a new blog post as a draft.

    Auto-generates a slug from the title and calculates reading time.
    Content is sanitized if HTML format.

    Issue #139: ``_ensure_unique_slug`` + the INSERT used to be a
    SELECT-then-INSERT race. Two concurrent saves with the same title
    both observed "no slug `hello-world`" exists, then both INSERTed
    `hello-world` and the second tripped ``UNIQUE(slug)``. The whole
    pair now runs inside an explicit ``BEGIN IMMEDIATE`` transaction
    (matches Phase 27.2's pattern; ``app.db._InstrumentedConnection``
    doesn't forward the context-manager protocol). On
    :class:`sqlite3.IntegrityError` we roll back and retry once with
    ``_ensure_unique_slug`` re-run — the racing INSERT has already
    taken ``<slug>`` by then, so the retry's slug is ``<slug>-2``.

    Returns:
        int: The new post's ID.
    """
    if content_format == 'html':
        content = sanitize_html(content)
    reading_time = _calculate_reading_time(content, content_format)

    base_slug = slugify(title)
    for attempt in range(_MAX_SLUG_RETRIES):
        db.execute('BEGIN IMMEDIATE')
        try:
            slug = _ensure_unique_slug(db, base_slug)
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
        except sqlite3.IntegrityError:
            db.rollback()
            if attempt == _MAX_SLUG_RETRIES - 1:
                raise
        except Exception:
            db.rollback()
            raise
    return None  # unreachable; loop either returns or re-raises


def update_post(
    db: sqlite3.Connection,
    post_id: int,
    title: str,
    summary: str = '',
    content: str = '',
    content_format: str = 'html',
    cover_image: str = '',
    author: str = '',
    tags: str = '',
    meta_description: str = '',
    featured: bool = False,
    slug: str | None = None,
) -> None:
    """Update an existing blog post.

    If slug is provided and different from auto-generated, uses the
    provided slug (after ensuring uniqueness). Recalculates reading time.

    Issue #140: the UPDATE + ``_sync_tags`` pair previously ran without
    verifying ``post_id`` existed. A stale form (post deleted by another
    admin between page load and save) used to silently UPDATE 0 rows
    and then have ``_sync_tags`` create orphan ``blog_post_tags``
    junction entries pointing at the missing id. The pair now runs
    inside ``BEGIN IMMEDIATE``; if ``cursor.rowcount`` is 0 we raise
    :class:`ValueError` and roll back so no orphan tags land.

    Issue #139: same SELECT-then-INSERT race shape as ``create_post``
    when the slug rename collides with a concurrent writer. Wrapped in
    the same retry-on-IntegrityError loop.

    Raises:
        ValueError: When ``post_id`` does not match an existing row.
    """
    if content_format == 'html':
        content = sanitize_html(content)
    reading_time = _calculate_reading_time(content, content_format)

    base_slug = slugify(slug) if slug else slugify(title)
    for attempt in range(_MAX_SLUG_RETRIES):
        db.execute('BEGIN IMMEDIATE')
        try:
            new_slug = _ensure_unique_slug(db, base_slug, exclude_id=post_id)
            cursor = db.execute(
                'UPDATE blog_posts SET slug=?, title=?, summary=?, content=?, '
                'content_format=?, cover_image=?, author=?, featured=?, '
                'reading_time=?, meta_description=?, '
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
                (
                    new_slug,
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
            if (cursor.rowcount or 0) == 0:
                raise ValueError(f'post not found: id={post_id}')

            _sync_tags(db, post_id, tags)
            db.commit()
            return
        except sqlite3.IntegrityError:
            db.rollback()
            if attempt == _MAX_SLUG_RETRIES - 1:
                raise
        except Exception:
            db.rollback()
            raise


def publish_post(db: sqlite3.Connection, post_id: int) -> None:
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


def unpublish_post(db: sqlite3.Connection, post_id: int) -> None:
    """Revert a published post to draft status."""
    db.execute(
        "UPDATE blog_posts SET status='draft', "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (post_id,),
    )
    db.commit()


def archive_post(db: sqlite3.Connection, post_id: int) -> None:
    """Archive a post (removes from public view but preserves content)."""
    db.execute(
        "UPDATE blog_posts SET status='archived', "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id=?",
        (post_id,),
    )
    db.commit()


def delete_post(db: sqlite3.Connection, post_id: int) -> None:
    """Permanently delete a post and its tag associations."""
    db.execute('DELETE FROM blog_post_tags WHERE post_id = ?', (post_id,))
    db.execute('DELETE FROM blog_posts WHERE id = ?', (post_id,))
    db.commit()
