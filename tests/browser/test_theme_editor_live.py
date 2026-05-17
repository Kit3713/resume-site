"""Phase 31 — Theme-editor accent change mirrors to the preview iframe.

Loads ``/admin/theme``, changes the accent color through the colour input,
then asserts that within 250 ms the iframe's ``document.documentElement``
inline style carries the new ``--color-accent`` value — without a full
reload (postMessage live-update path only).
"""

from __future__ import annotations

import time

from playwright.sync_api import Page, expect


def test_theme_editor_accent_mirrors_to_iframe(
    authenticated_page: Page,
    base_url: str,
) -> None:
    """Live-preview iframe receives the new accent within 250 ms."""
    page = authenticated_page
    page.goto(f'{base_url}/admin/theme', wait_until='networkidle')

    # The iframe loads ``?preview=1`` so the server emits SAMEORIGIN
    # X-Frame-Options (Phase 31 same-origin embed fix). Wait until the
    # parent JS can reach into the iframe's document — same-origin
    # access lights up once the document is parsed.
    page.wait_for_function(
        '() => {'
        '  const f = document.getElementById("preview-frame");'
        '  return f && f.contentDocument && f.contentDocument.documentElement;'
        '}',
        timeout=10000,
    )

    # Snapshot a stable signal we'll use to verify no reload occurred.
    initial_outerhtml_len = page.evaluate(
        "() => document.getElementById('preview-frame').contentDocument"
        '.documentElement.outerHTML.length'
    )

    # Choose a hex distinct from the default (#0071e3 = Apple blue).
    new_accent = '#ff5733'

    # Change the colour input. ``page.fill`` on input[type=color] doesn't
    # always dispatch ``input`` — set the value + dispatch the event
    # manually so the template's live listener fires.
    page.locator('#accent-color').evaluate(
        '(el, value) => {'
        '  el.value = value;'
        "  el.dispatchEvent(new Event('input', { bubbles: true }));"
        '}',
        new_accent,
    )

    # The hex sibling input should mirror first; verify the parent
    # JavaScript ran before we look inside the iframe.
    expect(page.locator('#accent-hex')).to_have_value(new_accent)

    # Poll the iframe's inline style for the new property. Budget is
    # 250 ms per the ROADMAP item.
    deadline = time.monotonic() + 0.25
    observed = ''
    while time.monotonic() < deadline:
        observed = page.evaluate(
            "() => document.getElementById('preview-frame').contentDocument"
            ".documentElement.style.getPropertyValue('--color-accent')"
        )
        if observed.strip().lower() == new_accent.lower():
            break
        page.wait_for_timeout(20)

    assert observed.strip().lower() == new_accent.lower(), (
        f'iframe --color-accent did not mirror within 250 ms: got {observed!r}'
    )

    # Confirm the iframe did NOT reload. A full reload re-parses the
    # entire document; the inline-style mutation only changes one
    # attribute (a few dozen bytes). A reload would shift the length by
    # hundreds or even reset it to a fresh-page baseline.
    post_outerhtml_len = page.evaluate(
        "() => document.getElementById('preview-frame').contentDocument"
        '.documentElement.outerHTML.length'
    )
    assert abs(post_outerhtml_len - initial_outerhtml_len) < 200, (
        'iframe appears to have fully reloaded — accent change should be live'
    )
