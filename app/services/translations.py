"""
Content Translation Service — Phase 15.2

Provides a translation-aware query layer for user-generated content.
Each translatable content type has a ``_translations`` junction table
(created by migration 011). This module resolves translations with
automatic fallback to the default locale, then to the original table
values.

Usage:
    from app.services.translations import get_translated, save_translation

    # Returns the Spanish version if available, else the English version,
    # else the original row values.
    row = get_translated(db, 'content_blocks', block_id, 'es')
"""

from __future__ import annotations

import sqlite3

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
        'fields': ('title', 'description'),
    },
    'certifications': {
        'table': 'certification_translations',
        'fk': 'cert_id',
        'fields': ('title', 'description'),
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

    coalesce_cols = ', '.join(
        f'COALESCE(NULLIF(t.{f}, ""), s.{f}) AS {f}' for f in fields
    )
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
