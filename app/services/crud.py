"""
CRUD Helper Service (app/services/crud.py)

Phase 29.2 (#56) — extracted from the partial-update + validation +
activity-log triad that the HTML admin services and the API services
duplicated on every update path.

The single helper below is :func:`update_fields`. It applies a
partial update to one row, optionally runs caller-supplied validation,
and optionally emits an admin activity-log entry — all wrapped in a
single explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``
transaction so the UPDATE and the log INSERT cannot diverge if a
concurrent writer races us.

Why a service-layer helper rather than a route-layer one:

* The duplication lives in the SQL shape (build dict, splice column
  names, bind values via ``?``, log on success). Routes contribute the
  validation closure and the activity-log strings; the service layer
  contributes the SQL.
* Phase 27.2's atomicity pattern (BEGIN IMMEDIATE / COMMIT / ROLLBACK
  rather than ``with db:``) is reused here because
  ``app.db._InstrumentedConnection`` doesn't surface the
  context-manager protocol of the underlying ``sqlite3.Connection``.

Migration scope for v0.3.3:

* :func:`app.services.webhooks.update_webhook` — was the closest match
  already; now a thin wrapper around this helper.
* :func:`app.services.service_items.update_service` — converted from
  always-update to partial-update.
* :func:`app.services.stats.update_stat` — same.

Other ``update_*`` functions in the service layer carry one-off quirks
(slug regeneration, content-format-conditional sanitisation, derived
``reading_time`` / ``published_at`` columns) and are tracked for
follow-up rather than force-fit through this helper.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


def update_fields(
    db: sqlite3.Connection,
    table: str,
    row_id: int,
    fields: dict,
    *,
    column_allowlist: set[str],
    validate: Callable[[dict], None] | None = None,
    activity_event: str | None = None,
    activity_category: str = '',
    activity_detail: str | None = None,
    activity_actor: str = 'admin',
    id_column: str = 'id',
) -> int:
    """Apply a partial update to a row.

    Wraps the UPDATE + optional activity-log INSERT in a single
    ``BEGIN IMMEDIATE`` transaction. If validation raises or the
    UPDATE fails, the activity-log INSERT does NOT land — there is no
    in-between state visible to a concurrent reader.

    Args:
        db: Database connection (Flask request-scoped or raw).
        table: Target table name. Must already be in
            :data:`column_allowlist` for the route's purposes — the
            helper does not maintain its own table allowlist; the
            caller is the trust boundary.
        row_id: Primary-key value of the row to update.
        fields: Mapping of column name → new value. Only the columns
            present here are written; columns NOT in the dict are left
            untouched. Empty / ``None`` values are written verbatim
            (callers strip empties upstream if that matters for the
            domain).
        column_allowlist: The set of column names this caller is
            permitted to write. Keys in ``fields`` not in the allowlist
            raise :class:`ValueError`.
        validate: Optional callable invoked as ``validate(fields)``
            before the UPDATE runs. Should raise :class:`ValueError`
            on bad input. Runs inside the transaction so a validation
            failure rolls back cleanly along with the UPDATE.
        activity_event: When truthy, an entry is written to
            ``admin_activity_log`` after the UPDATE succeeds. Stored as
            the activity-log ``action`` column.
        activity_category: Stored as the activity-log ``category``
            column. Empty string by default.
        activity_detail: Stored as the activity-log ``detail`` column.
            Defaults to ``f'id={row_id}'`` when not supplied so the log
            line is at least minimally identifiable.
        activity_actor: Stored as the activity-log ``admin_user``
            column.
        id_column: The primary-key column name. Defaults to ``id``;
            override for tables that use a non-default key (none in
            v0.3.3 do, but this keeps the contract honest).

    Returns:
        The number of rows updated (0 or 1 in normal use).

    Raises:
        ValueError: Either the dict is empty (nothing to update), a
            key is not in the allowlist, or the supplied ``validate``
            callable raised.
    """
    if not fields:
        # An empty dict is a caller bug — surface it loudly rather than
        # silently no-op'ing. The helper's transaction wraps a real
        # UPDATE; nothing to do here.
        raise ValueError('update_fields: empty fields dict — nothing to update')

    bad_keys = sorted(set(fields) - column_allowlist)
    if bad_keys:
        raise ValueError(f'update_fields: unknown column(s) {bad_keys!r} for table {table!r}')

    # Phase 27.2 atomicity pattern. Explicit BEGIN IMMEDIATE rather than
    # `with db:` because `app.db._InstrumentedConnection` doesn't
    # forward the context-manager protocol of sqlite3.Connection.
    db.execute('BEGIN IMMEDIATE')
    try:
        if validate is not None:
            validate(fields)

        # Build the SET clause from the dict keys. Column names are
        # already gated by the allowlist above, so splicing them into
        # the SQL string is safe; values bind through ``?`` placeholders
        # as normal. The bandit / S608 annotation documents the audit.
        columns = sorted(fields)
        set_clause = ', '.join(f'{col} = ?' for col in columns)
        params = [fields[col] for col in columns]
        params.append(row_id)
        # noqa / nosec: column allowlist enforced above; ``table`` and
        # ``id_column`` come from the caller's hardcoded service-layer
        # constants. Values bind through `?` placeholders as normal.
        sql = f'UPDATE {table} SET {set_clause} WHERE {id_column} = ?'  # noqa: S608  # nosec B608
        cursor = db.execute(sql, params)
        rowcount = cursor.rowcount or 0

        if activity_event:
            detail = activity_detail if activity_detail is not None else f'id={row_id}'
            # Inline the INSERT here rather than calling log_action —
            # log_action does its own commit, which would close our
            # transaction prematurely. The column list matches the
            # admin_activity_log schema (003_admin_customization.sql).
            db.execute(
                'INSERT INTO admin_activity_log '
                '(action, category, detail, admin_user) VALUES (?, ?, ?, ?)',
                (activity_event, activity_category, detail, activity_actor),
            )

        db.commit()
        return rowcount
    except Exception:
        db.rollback()
        raise
