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
    },
    'blog_posts': {
        'table': 'blog_post_translations',
        'fk': 'post_id',
        'fields': ('title', 'summary', 'content'),
    },
    'services': {
        'table': 'service_translations',
        'fk': 'service_id',
        'fields': ('title', 'description'),
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
    },
    'certifications': {
        'table': 'certification_translations',
        'fk': 'cert_id',
        # Mirrors the parent table's column names (``name``, not ``title``).
        # Phase 15.4 migration 011 update aligned both sides.
        'fields': ('name', 'description'),
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

    row = db.execute(f'SELECT * FROM {source_table} WHERE id = ?', (item_id,)).fetchone()  # noqa: S608
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


def save_translation(
    db: sqlite3.Connection,
    source_table: str,
    parent_id: int,
    locale: str,
    **fields: str,
) -> None:
    """Insert or update a translation for a specific item and locale."""
    config = _TRANSLATION_TABLES.get(source_table)
    if not config:
        return

    valid_fields = {k: v for k, v in fields.items() if k in config['fields']}
    if not valid_fields:
        return

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
    rows = db.execute(f'PRAGMA table_info({table})').fetchall()  # noqa: S608
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
