"""
Service Items Service (app/services/service_items.py)

Business logic for the services table (service cards on public pages).
Named service_items to avoid a naming collision with the services/ package.

Admin routes call these functions instead of writing SQL inline. The
description field is rendered with `| safe` in templates, so it is piped
through sanitize_html() on every write as defense in depth against XSS
(even though only the authenticated admin can write it).
"""

from app.exceptions import ValidationError
from app.services.content import sanitize_html


def get_all_services(db):
    """Return all service cards ordered by sort_order."""
    return db.execute('SELECT * FROM services ORDER BY sort_order').fetchall()


def add_service(db, title, description='', icon='', sort_order=0):
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


def update_service(db, service_id, title, description='', icon='', sort_order=0, visible=True):
    """Update an existing service card.

    Args:
        db: Database connection.
        service_id: The service's primary key.
        title: Service name.
        description: Body text (HTML, sanitized on write).
        icon: Emoji or icon identifier.
        sort_order: Display order.
        visible: Whether to show on the public site.
    """
    db.execute(
        'UPDATE services SET title = ?, description = ?, icon = ?, sort_order = ?, visible = ?, '
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (
            title.strip(),
            sanitize_html(description),
            icon,
            int(sort_order),
            1 if visible else 0,
            service_id,
        ),
    )
    db.commit()


def delete_service(db, service_id):
    """Delete a service card by ID."""
    db.execute('DELETE FROM services WHERE id = ?', (service_id,))
    db.commit()
