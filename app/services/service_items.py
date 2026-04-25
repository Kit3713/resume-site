"""
Service Items Service (app/services/service_items.py)

Business logic for the services table (service cards on public pages).
Named service_items to avoid a naming collision with the services/ package.

Admin routes call these functions instead of writing SQL inline. The
description field is rendered with `| safe` in templates, so it is piped
through sanitize_html() on every write as defense in depth against XSS
(even though only the authenticated admin can write it).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.exceptions import ValidationError
from app.services.content import sanitize_html
from app.services.crud import update_fields

#: Phase 29.2 (#56) — column allowlist consumed by
#: :func:`app.services.crud.update_fields`. ``updated_at`` is included
#: because :func:`update_service` overwrites it on every save (the
#: previous inline UPDATE bumped it via ``strftime('now')`` and we
#: preserve that behaviour so the admin dashboard's "last edited" sort
#: keeps working).
_SERVICE_COLUMNS = {'title', 'description', 'icon', 'sort_order', 'visible', 'updated_at'}


def get_all_services(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all service cards ordered by sort_order."""
    return db.execute('SELECT * FROM services ORDER BY sort_order').fetchall()


def add_service(
    db: sqlite3.Connection,
    title: str,
    description: str = '',
    icon: str = '',
    sort_order: int = 0,
) -> None:
    """Insert a new service card.

    Args:
        db: Database connection.
        title: Service name (required).
        description: Body text shown on the card (HTML, sanitized on write).
        icon: Emoji or icon identifier.
        sort_order: Display order (lower = earlier).
    """
    if not title:
        raise ValidationError('Service title cannot be empty.')
    db.execute(
        'INSERT INTO services (title, description, icon, sort_order) VALUES (?, ?, ?, ?)',
        (title.strip(), sanitize_html(description), icon, int(sort_order)),
    )
    db.commit()


def update_service(
    db: sqlite3.Connection,
    service_id: int,
    title: str,
    description: str = '',
    icon: str = '',
    sort_order: int = 0,
    visible: bool = True,
) -> None:
    """Update an existing service card.

    Args:
        db: Database connection.
        service_id: The service's primary key.
        title: Service name.
        description: Body text (HTML, sanitized on write).
        icon: Emoji or icon identifier.
        sort_order: Display order.
        visible: Whether to show on the public site.

    Phase 29.2 (#56) — delegates to :func:`app.services.crud.update_fields`
    so the column-name allowlist + UPDATE + activity-log INSERT all live
    behind one transaction. The caller contract is unchanged.
    """
    update_fields(
        db,
        'services',
        service_id,
        {
            'title': title.strip(),
            'description': sanitize_html(description),
            'icon': icon,
            'sort_order': int(sort_order),
            'visible': 1 if visible else 0,
            'updated_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        },
        column_allowlist=_SERVICE_COLUMNS,
    )


def delete_service(db: sqlite3.Connection, service_id: int) -> None:
    """Delete a service card by ID."""
    db.execute('DELETE FROM services WHERE id = ?', (service_id,))
    db.commit()
