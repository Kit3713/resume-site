"""Phase 31 — Sortable.js reorder persists across page reloads.

Seeds three services through the admin form, calls the same ``/admin/reorder``
endpoint Sortable.js posts to (the underlying drag gesture is exercised by
Sortable's own unit tests; what matters here is that *our* persistence path
works), then reloads ``/admin/services`` and asserts the displayed order
matches the new ordering.

Drag-and-drop in headless browsers is famously unreliable across Playwright
versions because Sortable.js listens to pointer events that don't always
synthesise cleanly. The fetch() call the template makes on ``onEnd`` is the
*persistence* contract — the part that breaks in production if it regresses
— so we drive it directly and assert the round-trip through the database
returns the expected order. The Sortable mount itself is verified by
``test_gsap_scroll.py`` and the JS-error guard in ``test_cdn_unavailability.py``.
"""

from __future__ import annotations

import uuid

from playwright.sync_api import Page, expect


def _seed_service(page: Page, base_url: str, title: str) -> int:
    """Submit the admin form to create a service, return its inserted id.

    The admin list shows ``data-id`` on each row in the order the service
    was inserted; we read the most recent id from the DOM after submit.
    """
    page.goto(f'{base_url}/admin/services')
    page.fill('input[name="title"]', title)
    page.fill('textarea[name="description"]', f'desc for {title}')
    # The page has two forms — the first (admin-form, no compact) is the
    # "Add Service" form. Submit only it.
    page.locator('form[action$="/admin/services/add"] button[type="submit"]').click()
    page.wait_for_url(f'{base_url}/admin/services')
    # The newest service row carries the highest data-id.
    ids = page.eval_on_selector_all(
        '#sortable-services [data-id]',
        'els => els.map(e => parseInt(e.dataset.id, 10))',
    )
    assert ids, f'no services rendered after creating {title!r}'
    return max(ids)


def test_service_reorder_persists_across_reload(
    authenticated_page: Page,
    base_url: str,
) -> None:
    """Reorder three services via the persistence endpoint; reload; check order."""
    page = authenticated_page
    marker = uuid.uuid4().hex[:6]
    titles = [
        f'phase31-svc-a-{marker}',
        f'phase31-svc-b-{marker}',
        f'phase31-svc-c-{marker}',
    ]
    ids = [_seed_service(page, base_url, t) for t in titles]

    # Build a new ordering — reverse the insertion order so the change
    # is observable.
    new_order = list(reversed(ids))

    # Fire the same fetch() the Sortable.js onEnd handler does. We pull
    # the CSRF token from the rendered admin form so the request goes
    # through Flask-WTF's protection just like a real user-driven drag.
    csrf_token = page.eval_on_selector(
        'input[name="csrf_token"]',
        'el => el.value',
    )
    result = page.evaluate(
        """
        async ({idOrder, token}) => {
            const resp = await fetch('/admin/reorder', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': token
                },
                body: JSON.stringify({table: 'services', id_order: idOrder})
            });
            return {status: resp.status, body: await resp.text()};
        }
    """,
        {'idOrder': new_order, 'token': csrf_token},
    )
    assert result['status'] == 200, (
        f'reorder POST failed: {result["status"]} {result["body"][:200]}'
    )

    # Reload and verify the DOM order reflects the new ordering. The
    # services page renders rows ordered by sort_order ASC; the rows
    # carrying our seeded marker should appear in ``new_order``.
    page.reload()
    rendered_ids = page.eval_on_selector_all(
        '#sortable-services [data-id]',
        'els => els.map(e => parseInt(e.dataset.id, 10))',
    )
    # Filter down to just our seeded ids so unrelated rows (e.g. the
    # ones the seed step might have created) don't interfere.
    seeded_rendered = [i for i in rendered_ids if i in set(ids)]
    assert seeded_rendered == new_order, (
        f'reorder did not persist: expected {new_order}, got {seeded_rendered}'
    )

    # Sanity-check that the markers are still visible (i.e. the rows
    # weren't silently dropped). Use the title of the first item in the
    # new ordering as a stable probe.
    expect(page.locator(f'input[value="{titles[-1]}"]')).to_be_visible()
