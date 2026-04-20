"""
Content Translation Service — Phase 15.2 / 15.4

Provides a translation-aware query layer for user-generated content.
Each translatable content type has a ``_translations`` junction table
(created by migration 011). This module resolves translations with
automatic fallback to the default locale, then to the original table
values.

Phase 15.4 extends this with locale-aware wrappers around the public
model queries (services, stats, projects, certifications, content
blocks, blog posts). When the requested locale matches the default
locale the wrappers short-circuit to the original query — no JOIN
cost on the happy path.

Usage:
    from app.services.translations import get_translated, save_translation

    # Returns the Spanish version if available, else the English version,
    # else the original row values.
    row = get_translated(db, 'content_blocks', block_id, 'es')
"""

from __future__ import annotations

import sqlite3

# Open Graph locale codes use BCP 47 style (language_REGION). Browsers
# and social-media crawlers expect this specific format. Maps the short
# ISO 639-1 codes used in the settings table to the OG-compatible form.
_OG_LOCALE_MAP = {
    'en': 'en_US',
    'es': 'es_ES',
    'fr': 'fr_FR',
    'de': 'de_DE',
    'it': 'it_IT',
    'pt': 'pt_PT',
    'nl': 'nl_NL',
    'ja': 'ja_JP',
    'zh': 'zh_CN',
    'ko': 'ko_KR',
    'ru': 'ru_RU',
    'ar': 'ar_AR',
    'pl': 'pl_PL',
    'sv': 'sv_SE',
    'no': 'no_NO',
    'da': 'da_DK',
    'fi': 'fi_FI',
    'tr': 'tr_TR',
}


def og_locale(locale: str) -> str:
    """Convert an ISO 639-1 locale code to Open Graph form (``en_US``).

    Unknown short codes are mapped to ``<code>_<CODE>`` as a best-effort
    fallback — Facebook / LinkedIn tolerate that shape even when the
    specific region isn't recognised.
    """
    if not locale:
        return 'en_US'
    locale = locale.strip().lower()
    if locale in _OG_LOCALE_MAP:
        return _OG_LOCALE_MAP[locale]
    # Already region-qualified (e.g. 'pt-BR') — normalise to underscore
    if '-' in locale or '_' in locale:
        parts = locale.replace('-', '_').split('_')
        if len(parts) == 2:
            return f'{parts[0].lower()}_{parts[1].upper()}'
    return f'{locale}_{locale.upper()}'


_TRANSLATION_TABLES = {
    'content_blocks': {
        'table': 'content_block_translations',
        'fk': 'block_id',
        'fields': ('title', 'content', 'plain_text'),
        # Phase 22.2 (#41): HTML-format fields that must run through
        # ``sanitize_html`` at translation-save time, mirroring what the
        # default-locale save path does. Plain-text fields are omitted
        # (Jinja autoescape covers them on render).
        'html_fields': ('content',),
    },
    'blog_posts': {
        'table': 'blog_post_translations',
        'fk': 'post_id',
        'fields': ('title', 'summary', 'content'),
        # ``content`` is HTML only when the parent blog post is stored
        # with ``content_format = 'html'``. Markdown posts are rendered
        # through the blog service's own sanitising pipeline.
        'html_fields_when_parent_format_html': ('content',),
    },
    'services': {
        'table': 'service_translations',
        'fk': 'service_id',
        'fields': ('title', 'description'),
        'html_fields': ('description',),
    },
    'stats': {
        'table': 'stat_translations',
        'fk': 'stat_id',
        'fields': ('label', 'suffix'),
    },
    'projects': {
        'table': 'project_translations',
        'fk': 'project_id',
        # ``summary`` is the card-grid blurb; ``description`` is the
        # long-form body on the detail page. Both matter to visitors.
        'fields': ('title', 'summary', 'description'),
        'html_fields': ('description',),
    },
    'certifications': {
        'table': 'certification_translations',
        'fk': 'cert_id',
        # Mirrors the parent table's column names (``name``, not ``title``).
        # Phase 15.4 migration 011 update aligned both sides.
        'fields': ('name', 'description'),
        'html_fields': ('description',),
    },
}


def get_translated(
    db: sqlite3.Connection,
    source_table: str,
    item_id: int,
    locale: str,
    fallback_locale: str = 'en',
) -> dict | None:
    """Return a single item with translated fields overlaid.

    Looks up the translation for ``locale``, falls back to
    ``fallback_locale``, then to the original table values.
    Returns a plain dict (not a Row) with all columns from the source
    table, with translated fields overwritten where available.
    """
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return None

    row = db.execute(
        f'SELECT * FROM {source_table} WHERE id = ?',  # noqa: S608  # nosec B608 — source_table keyed from _TRANSLATION_TABLES dict literal
        (item_id,),
    ).fetchone()
    if not row:
        return None

    result = dict(row)

    for loc in (locale, fallback_locale):
        if not loc:
            continue
        trans = db.execute(
            f'SELECT * FROM {config["table"]} WHERE {config["fk"]} = ? AND locale = ?',  # noqa: S608
            (item_id, loc),
        ).fetchone()
        if trans:
            for field in config['fields']:
                val = trans[field]
                if val:
                    result[field] = val
            result['_locale'] = loc
            break

    return result


def get_all_translated(
    db: sqlite3.Connection,
    source_table: str,
    locale: str,
    fallback_locale: str = 'en',
    **filters: str,
) -> list[dict]:
    """Return all items from a source table with translations overlaid.

    Uses a single LEFT JOIN query to avoid N+1. Falls back to original
    values when no translation exists.
    """
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return []

    trans_table = config['table']
    fk = config['fk']
    fields = config['fields']

    coalesce_cols = ', '.join(f'COALESCE(NULLIF(t.{f}, ""), s.{f}) AS {f}' for f in fields)
    non_trans_cols = ', '.join(
        f's.{col}' for col in _get_column_names(db, source_table) if col not in fields
    )

    where_clauses = ['1=1']
    params: list = []
    for col, val in filters.items():
        where_clauses.append(f's.{col} = ?')
        params.append(val)

    sql = (
        f'SELECT {non_trans_cols}, {coalesce_cols} '  # noqa: S608
        f'FROM {source_table} s '
        f'LEFT JOIN {trans_table} t ON t.{fk} = s.id AND t.locale = ? '
        f'WHERE {" AND ".join(where_clauses)} '
        f'ORDER BY s.sort_order, s.id'
    )
    params = [locale, *params]

    return [dict(row) for row in db.execute(sql, params).fetchall()]


def _sanitize_translation_fields(
    db: sqlite3.Connection,
    source_table: str,
    parent_id: int,
    valid_fields: dict[str, str],
) -> dict[str, str]:
    """Apply :func:`app.services.content.sanitize_html` to every HTML-format
    translatable field (Phase 22.2 #41).

    Mirrors the sanitisation the default-locale save path already does
    so a per-locale save can't be the one place XSS slips through.
    Plain-text fields are copied verbatim — Jinja autoescape is the
    defence on render for those.

    The blog-post ``content`` field is conditionally HTML: we consult
    the parent row's ``content_format`` to decide. Markdown posts are
    re-rendered by ``app.services.blog.render_post_content``, which
    does its own sanitisation, so skipping here is safe.
    """
    from app.services.content import sanitize_html

    config = _TRANSLATION_TABLES.get(source_table) or {}
    html_fields = set(config.get('html_fields', ()))
    if 'html_fields_when_parent_format_html' in config:
        row = db.execute(
            f'SELECT content_format FROM {source_table} WHERE id = ?',  # noqa: S608  # nosec B608 — table keyed from dict literal
            (parent_id,),
        ).fetchone()
        fmt = (row['content_format'] if hasattr(row, 'keys') else row[0]) if row is not None else ''
        if fmt == 'html':
            html_fields.update(config['html_fields_when_parent_format_html'])

    sanitised = {}
    for key, value in valid_fields.items():
        if key in html_fields and value:
            sanitised[key] = sanitize_html(value)
        else:
            sanitised[key] = value
    return sanitised


def save_translation(
    db: sqlite3.Connection,
    source_table: str,
    parent_id: int,
    locale: str,
    **fields: str,
) -> None:
    """Insert or update a translation for a specific item and locale.

    Phase 22.2 (#41) — HTML-format fields are run through the same
    ``sanitize_html`` policy the default-locale save paths use. Callers
    can therefore pass raw form input without risking that a per-locale
    save becomes the XSS smuggle path.
    """
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return

    valid_fields = {k: v for k, v in fields.items() if k in config['fields']}
    if not valid_fields:
        return

    valid_fields = _sanitize_translation_fields(db, source_table, parent_id, valid_fields)

    trans_table = config['table']
    fk = config['fk']

    existing = db.execute(
        f'SELECT id FROM {trans_table} WHERE {fk} = ? AND locale = ?',  # noqa: S608
        (parent_id, locale),
    ).fetchone()

    if existing:
        set_clause = ', '.join(f'{k} = ?' for k in valid_fields)
        db.execute(
            f'UPDATE {trans_table} SET {set_clause} WHERE id = ?',  # noqa: S608
            [*valid_fields.values(), existing['id']],
        )
    else:
        cols = [fk, 'locale', *valid_fields.keys()]
        placeholders = ', '.join('?' * len(cols))
        db.execute(
            f'INSERT INTO {trans_table} ({", ".join(cols)}) VALUES ({placeholders})',  # noqa: S608
            [parent_id, locale, *valid_fields.values()],
        )


def delete_translation(
    db: sqlite3.Connection,
    source_table: str,
    parent_id: int,
    locale: str,
) -> None:
    """Remove a single locale's translation for an item."""
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return
    db.execute(
        f'DELETE FROM {config["table"]} WHERE {config["fk"]} = ? AND locale = ?',  # noqa: S608
        (parent_id, locale),
    )


def get_available_translations(
    db: sqlite3.Connection,
    source_table: str,
    parent_id: int,
) -> list[str]:
    """Return the list of locale codes that have translations for an item."""
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return []
    rows = db.execute(
        f'SELECT locale FROM {config["table"]} WHERE {config["fk"]} = ? ORDER BY locale',  # noqa: S608
        (parent_id,),
    ).fetchall()
    return [row['locale'] for row in rows]


def _get_column_names(db: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table via PRAGMA."""
    rows = db.execute(f'PRAGMA table_info({table})').fetchall()  # noqa: S608  # nosec B608 — table keyed from _TRANSLATION_TABLES dict literal
    return [row['name'] for row in rows]


# ============================================================
# Phase 15.4 — Locale-aware public query wrappers
# ============================================================
#
# Every wrapper short-circuits to the original query when the caller's
# locale matches the default. This keeps the N+1-free contract of
# ``get_all_translated`` from costing a LEFT JOIN on every visitor
# request in single-locale deployments.


def _should_translate(locale: str, fallback_locale: str) -> bool:
    """True when we need to run through the translation overlay."""
    return bool(locale) and locale != fallback_locale


def get_visible_services_for_locale(
    db: sqlite3.Connection,
    locale: str,
    fallback_locale: str = 'en',
) -> list:
    """Return visible services with per-locale overlay (Phase 15.4)."""
    from app.models import get_visible_services

    if not _should_translate(locale, fallback_locale):
        return get_visible_services(db)
    return get_all_translated(db, 'services', locale, fallback_locale, visible=1)


def get_visible_stats_for_locale(
    db: sqlite3.Connection,
    locale: str,
    fallback_locale: str = 'en',
) -> list:
    """Return visible stats with per-locale overlay (Phase 15.4)."""
    from app.models import get_visible_stats

    if not _should_translate(locale, fallback_locale):
        return get_visible_stats(db)
    return get_all_translated(db, 'stats', locale, fallback_locale, visible=1)


def get_visible_projects_for_locale(
    db: sqlite3.Connection,
    locale: str,
    fallback_locale: str = 'en',
) -> list:
    """Return visible projects with per-locale overlay (Phase 15.4)."""
    from app.models import get_visible_projects

    if not _should_translate(locale, fallback_locale):
        return get_visible_projects(db)
    return get_all_translated(db, 'projects', locale, fallback_locale, visible=1)


def get_visible_certifications_for_locale(
    db: sqlite3.Connection,
    locale: str,
    fallback_locale: str = 'en',
) -> list:
    """Return visible certifications with per-locale overlay (Phase 15.4)."""
    from app.models import get_visible_certifications

    if not _should_translate(locale, fallback_locale):
        return get_visible_certifications(db)
    return get_all_translated(db, 'certifications', locale, fallback_locale, visible=1)


def get_content_block_for_locale(
    db: sqlite3.Connection,
    slug: str,
    locale: str,
    fallback_locale: str = 'en',
):
    """Return a content block with per-locale overlay (Phase 15.4).

    Resolves the block by slug, then applies the translation. Returns
    ``None`` if the slug is unknown. The result type is ``sqlite3.Row``
    on the fast path and ``dict`` when translation runs — both support
    item access so templates don't need to care which they receive.
    """
    from app.models import get_content_block

    row = get_content_block(db, slug)
    if row is None:
        return None
    if not _should_translate(locale, fallback_locale):
        return row
    return get_translated(db, 'content_blocks', row['id'], locale, fallback_locale)


def overlay_post_translation(
    db: sqlite3.Connection,
    post,
    locale: str,
    fallback_locale: str = 'en',
):
    """Apply a blog-post translation overlay, returning a dict.

    ``post`` is a ``sqlite3.Row`` loaded via the normal blog service.
    When the active locale differs from the default, the post's
    translated title / summary / content are substituted. On the fast
    path the original row is returned unchanged.
    """
    if post is None or not _should_translate(locale, fallback_locale):
        return post
    return get_translated(db, 'blog_posts', post['id'], locale, fallback_locale) or post


def overlay_posts_translations(
    db: sqlite3.Connection,
    posts,
    locale: str,
    fallback_locale: str = 'en',
) -> list:
    """Apply translations to a paginated post listing (Phase 15.4).

    Returns a list — either the original rows unchanged (fast path) or
    dicts with the translated fields overlaid. The shape matches what
    ``_attach_tags`` in ``routes/blog.py`` expects.
    """
    if not posts or not _should_translate(locale, fallback_locale):
        return list(posts)
    return [overlay_post_translation(db, p, locale, fallback_locale) for p in posts]


def get_available_post_locales(
    db: sqlite3.Connection,
    post_id: int,
    default_locale: str = 'en',
) -> list[str]:
    """Return the locale codes with published translations for a post.

    Always includes ``default_locale`` because the original row stands in
    for it. Used for ``og:locale:alternate`` emission on single-post
    pages.
    """
    translated = get_available_translations(db, 'blog_posts', post_id)
    locales = {default_locale, *translated}
    return sorted(locales)


def get_coverage_matrix(
    db: sqlite3.Connection,
    configured_locales: list[str],
    default_locale: str = 'en',
) -> list[dict]:
    """Per-locale translation coverage matrix for the admin dashboard (36.3).

    Returns one row per translatable content type. Each row carries the
    total count in the parent table plus, for every configured locale,
    the number of rows that have a translation entry. The default locale
    is reported as fully covered because the parent table's own columns
    ARE the default-locale content.

    Runs one aggregate query per content type — bounded (six types, six
    queries) and cheap on SQLite thanks to the UNIQUE(parent_id, locale)
    index on every junction table.
    """
    matrix = []
    non_default = [loc for loc in configured_locales if loc and loc != default_locale]
    for source_table, cfg in _TRANSLATION_TABLES.items():
        total_row = db.execute(
            f'SELECT COUNT(*) AS cnt FROM {source_table}'  # noqa: S608 — source_table from literal dict keys
        ).fetchone()
        total = int(total_row['cnt']) if total_row else 0
        coverage: dict[str, int] = {default_locale: total}
        if non_default:
            if total:
                placeholders = ','.join('?' for _ in non_default)
                rows = db.execute(
                    f'SELECT locale, COUNT(*) AS cnt FROM {cfg["table"]} '  # noqa: S608 — table from literal dict; locales parameterised
                    f'WHERE locale IN ({placeholders}) GROUP BY locale',
                    tuple(non_default),
                ).fetchall()
                counts = {r['locale']: int(r['cnt']) for r in rows}
            else:
                counts = {}
            for loc in non_default:
                coverage[loc] = counts.get(loc, 0)
        matrix.append(
            {
                'type': source_table,
                'total': total,
                'coverage': coverage,
            }
        )
    return matrix
