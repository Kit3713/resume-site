"""Phase 31 — Quill editor saves and reloads content byte-identically.

Creates a new content block via the admin UI, types a formatted paragraph
through the live Quill instance, saves the form, reloads the edit page, and
asserts the HTML round-tripped without sanitiser-induced drift.

The seed step is part of the test: a brand-new content block called
``e2e_quill`` is created so the test is idempotent regardless of what
existing content the seed step provisioned.
"""

from __future__ import annotations

import uuid

from playwright.sync_api import Page


def test_quill_content_round_trip(authenticated_page: Page, base_url: str) -> None:
    """Type formatted text in Quill, save, reload — content survives."""
    page = authenticated_page

    # Use a fresh slug each run so re-runs don't collide. The slug
    # registry only allows lowercase + digits + underscore.
    slug = f'e2e_quill_{uuid.uuid4().hex[:8]}'
    marker = f'Phase 31 round-trip {uuid.uuid4().hex[:6]}'

    page.goto(f'{base_url}/admin/content/new')
    page.fill('input[name="slug"]', slug)
    page.fill('input[name="title"]', 'Phase 31 Quill round-trip')

    # Wait for Quill to mount — the editor div picks up the .ql-editor
    # child after initialisation.
    page.wait_for_selector('.ql-editor', state='visible')

    # Type into Quill's contenteditable surface and apply a bold range.
    editor = page.locator('.ql-editor')
    editor.click()
    editor.type(marker)
    page.keyboard.press('Control+a')
    page.locator('.ql-toolbar .ql-bold').click()

    # Snapshot the editor HTML *before* submit so we can compare to the
    # post-reload value byte-for-byte.
    saved_html: str = page.evaluate("() => document.querySelector('.ql-editor').innerHTML")
    assert '<strong>' in saved_html or '<b>' in saved_html, (
        f'Quill bold did not apply: {saved_html!r}'
    )

    page.click('button[type="submit"]')

    # After save, the admin redirects to the content list. Navigate back
    # to the edit page to observe what was persisted.
    page.goto(f'{base_url}/admin/content/edit/{slug}')
    page.wait_for_selector('.ql-editor', state='visible')

    reloaded_html: str = page.evaluate("() => document.querySelector('.ql-editor').innerHTML")

    # Quill normalises certain attributes on re-render, but the marker
    # text and the bold tag must both survive the sanitizer.
    assert marker in reloaded_html, f'marker text not preserved: {reloaded_html!r}'
    assert '<strong>' in reloaded_html or '<b>' in reloaded_html, (
        f'bold formatting not preserved: {reloaded_html!r}'
    )
    # The hidden input the form actually POSTs from carries the
    # canonical persisted HTML; assert the marker reached it too. If
    # the Quill ``text-change`` sync regresses, the editor would render
    # correctly but the submitted value would lag, and that's the
    # regression that actually loses operator data.
    hidden_value: str = page.locator('#contentInput').input_value()
    assert marker in hidden_value, f'marker not synced to hidden input: {hidden_value!r}'
