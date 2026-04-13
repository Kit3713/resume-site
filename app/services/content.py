"""
Content Block Service (app/services/content.py)

Business logic for the content_blocks table. Admin routes call these
functions instead of writing SQL inline, making them independently
testable and keeping the routes as thin controllers.

HTML sanitization is applied on every write using nh3, which enforces
a strict allowlist of safe tags. This prevents XSS payloads stored via
the Quill.js editor from being rendered to public visitors.
"""

try:
    import nh3

    _HAS_NH3 = True
except ImportError:
    _HAS_NH3 = False

# Tags that the Quill editor legitimately produces and we allow in storage.
_ALLOWED_TAGS = {
    'p',
    'br',
    'strong',
    'em',
    'u',
    's',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'ul',
    'ol',
    'li',
    'blockquote',
    'pre',
    'code',
    'a',
    'img',
    'span',
    'div',
}

_ALLOWED_ATTRS = {
    'a': {'href', 'target', 'rel'},
    'img': {'src', 'alt', 'width', 'height'},
    '*': {'class'},
}


def sanitize_html(html: str) -> str:
    """Strip disallowed tags/attributes from HTML using nh3.

    Falls back to returning the input unchanged if nh3 is not installed
    (allows the app to run without the dependency during development, but
    nh3 should always be present in production — see requirements.txt).
    """
    if not _HAS_NH3 or not html:
        return html
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        link_rel=None,  # Don't forcibly add rel="noopener" — we control content
    )


def get_all_blocks(db):
    """Return all content blocks ordered by sort_order."""
    return db.execute('SELECT * FROM content_blocks ORDER BY sort_order').fetchall()


def get_block_by_slug(db, slug):
    """Return a single content block by slug, or None."""
    return db.execute('SELECT * FROM content_blocks WHERE slug = ?', (slug,)).fetchone()


def save_block(db, slug, title, content_html, create_if_missing=True):
    """Save (create or update) a content block.

    Sanitizes HTML before storage. If the slug already exists, the
    existing row is updated. If not, a new row is inserted (when
    create_if_missing=True).

    Args:
        db: Database connection.
        slug: The unique block identifier.
        title: Admin-facing label for the block.
        content_html: Raw HTML from Quill editor (will be sanitized).
        create_if_missing: Insert a new row if slug not found.
    """
    safe_html = sanitize_html(content_html)
    existing = get_block_by_slug(db, slug)

    if existing:
        db.execute(
            'UPDATE content_blocks SET title = ?, content = ?, '
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE slug = ?",
            (title, safe_html, slug),
        )
    elif create_if_missing:
        db.execute(
            'INSERT OR IGNORE INTO content_blocks (slug, title, content) VALUES (?, ?, ?)',
            (slug, title, safe_html),
        )

    db.commit()


def create_block(db, slug, title, content_html):
    """Create a new content block with a given slug.

    The slug is normalized: lowercased and spaces replaced with underscores.
    Uses INSERT OR IGNORE so duplicate slugs silently no-op.
    """
    slug = slug.strip().lower().replace(' ', '_')
    safe_html = sanitize_html(content_html)
    db.execute(
        'INSERT OR IGNORE INTO content_blocks (slug, title, content) VALUES (?, ?, ?)',
        (slug, title, safe_html),
    )
    db.commit()
    return slug
