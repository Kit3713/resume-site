"""Phase 31 — Dark/light theme toggle persists across reloads.

Sets ``localStorage.theme = 'light'`` programmatically, reloads, then asserts
that the anti-FOUC bootstrap script in ``base.html`` has applied
``data-theme="light"`` on ``<html>`` and that the resolved ``--color-bg``
matches the light-theme custom property (``#ffffff``).
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


def test_theme_toggle_persists_after_reload(page: Page, base_url: str) -> None:
    """Light theme stored in localStorage survives a full page reload."""
    page.goto(base_url)
    # Seed localStorage and reload — the anti-FOUC inline script reads the
    # value before paint and sets data-theme accordingly. Doing this after
    # a goto ensures the storage origin matches the page origin.
    page.evaluate("() => localStorage.setItem('theme', 'light')")
    page.reload()

    html = page.locator('html')
    expect(html).to_have_attribute('data-theme', 'light')

    # Verify the computed background colour resolves to the light-theme
    # custom property (#ffffff). The check is structural, not pixel-level:
    # if the data-theme attribute changes but the CSS variable cascade is
    # broken, the colour won't match.
    bg = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()"
    )
    assert bg == '#ffffff', f'expected --color-bg=#ffffff, got {bg!r}'
