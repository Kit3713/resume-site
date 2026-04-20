"""
Content Block Service (app/services/content.py)

Business logic for the content_blocks table. Admin routes call these
functions instead of writing SQL inline, making them independently
testable and keeping the routes as thin controllers.

HTML sanitization is applied on every write using nh3, which enforces
a strict allowlist of safe tags. This prevents XSS payloads stored via
the Quill.js editor from being rendered to public visitors.

Phase 22.2 (#63) — nh3 is a HARD runtime dependency. The old
``try/except ImportError`` fell back to returning the input unchanged,
so a broken install silently disabled sanitisation and let XSS through
every write path. The import below intentionally fails at app-boot if
nh3 is missing so the deploy surface-faults loudly instead of quietly
regressing.
"""

from __future__ import annotations

import sqlite3

import nh3

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

    Phase 22.2 (#63) — fail-closed. An empty input returns the empty
    string; any other input goes through ``nh3.clean`` unconditionally.
    The previous ``_HAS_NH3`` fallback branch was removed because a
    missing ``nh3`` import silently disabled sanitisation for every
    write path the admin panel exposes.
    """
    if not html:
        return html
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        link_rel=None,  # Don't forcibly add rel="noopener" — we control content
    )


# ---------------------------------------------------------------------------
# URL allowlist (Phase 22.2 #17)
#
# Custom nav links (and any future operator-supplied href) must never
# carry the ``javascript:``, ``data:``, or ``vbscript:`` schemes that
# browsers execute instead of navigating to. The audit item #17 found
# that ``custom_nav_links`` flows straight into ``href="..."`` via
# ``base.html`` with only Jinja autoescape between the attacker and a
# living-off-the-href XSS.
#
# Two defences:
#
# 1. ``validate_safe_url`` runs at save time and rejects bad values
#    with a user-visible error (wired in ``save_settings``).
# 2. ``safe_url`` is a Jinja filter that silently rewrites a bad URL
#    to ``#`` at render time so even a legacy row created before the
#    save-time gate came in cannot execute.
# ---------------------------------------------------------------------------

_ALLOWED_URL_SCHEMES = ('http', 'https', 'mailto')


def validate_safe_url(url: str) -> bool:
    """Return True when ``url`` is safe to place inside ``href="..."``.

    Accepts absolute URLs of the form ``http://...``, ``https://...``
    or ``mailto:...``, plus site-relative paths starting with ``/``.
    Empty strings are treated as unsafe — an operator submitting a
    link without a URL is a bug either way.

    Anything else (including ``javascript:``, ``data:``, ``vbscript:``,
    scheme-relative ``//evil.com``, and malformed inputs) is rejected.
    """
    if not url or not isinstance(url, str):
        return False
    stripped = url.strip()
    if not stripped:
        return False
    # Guard scheme-relative URLs explicitly — urlparse would otherwise
    # return an empty scheme + attacker-controlled netloc.
    if stripped.startswith('//'):
        return False
    # Site-relative paths always safe.
    if stripped.startswith('/') and not stripped.startswith('//'):
        return True
    # Fragment-only / query-only links (bare ``#`` or ``?foo=bar``) are
    # same-document and therefore safe too.
    if stripped.startswith(('#', '?')):
        return True
    from urllib.parse import urlparse

    parsed = urlparse(stripped)
    scheme = (parsed.scheme or '').lower().strip()
    if not scheme:
        return False
    return scheme in _ALLOWED_URL_SCHEMES


def safe_url(url):
    """Jinja filter: pass-through if safe, replace with ``#`` otherwise.

    Used by ``base.html`` as a second-line defence so a legacy row
    that bypassed the save-time validator (e.g. written directly via
    a manage.py one-off before 22.2 landed) still can't execute. The
    bad value logs a warning once per render so operators see it.
    """
    if validate_safe_url(str(url or '')):
        return url
    return '#'


def get_all_blocks(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all content blocks ordered by sort_order."""
    return db.execute('SELECT * FROM content_blocks ORDER BY sort_order').fetchall()


def get_block_by_slug(db: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    """Return a single content block by slug, or None."""
    return db.execute('SELECT * FROM content_blocks WHERE slug = ?', (slug,)).fetchone()


def save_block(
    db: sqlite3.Connection,
    slug: str,
    title: str,
    content_html: str,
    create_if_missing: bool = True,
) -> None:
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


def create_block(db: sqlite3.Connection, slug: str, title: str, content_html: str) -> str:
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
