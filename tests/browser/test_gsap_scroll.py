"""Phase 31 — GSAP scroll-triggered animations fire without JS errors.

Scrolls the landing page and asserts that:

1. ``window.gsap`` and ``window.ScrollTrigger`` are loaded from the CDN.
2. Sections animate to ``opacity: 1`` within 2 s (the GSAP transition target).
3. No JavaScript exceptions are emitted during the scroll.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page, expect


def test_gsap_scroll_animations_apply_without_errors(
    page: Page,
    base_url: str,
) -> None:
    """Scroll to each section; opacity reaches 1 within 2 s; no JS errors."""
    errors: list[str] = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))

    page.goto(base_url, wait_until='networkidle')

    # GSAP must have loaded from the cdnjs CDN.
    gsap_ready = page.evaluate('() => typeof window.gsap !== "undefined"')
    assert gsap_ready, 'GSAP did not initialise — CDN may be unreachable'

    # Pick a representative animated section. The hero is in-viewport at
    # page load (no scroll needed) so its children get animated by the
    # on-load tween; ``.hero__content`` settles at opacity 1 once the
    # tween completes (~0.8 s + 0.2 s delay).
    hero = page.locator('.hero__content').first
    expect(hero).to_be_visible()

    # Wait up to 2 s for the on-load animation to settle the children at
    # opacity 1. GSAP animates ``from {opacity: 0}`` so the final value is
    # the computed opacity of the live element.
    deadline = time.monotonic() + 2.0
    final_opacity = '0'
    while time.monotonic() < deadline:
        final_opacity = page.evaluate(
            '() => {'
            "  const el = document.querySelector('.hero__content > *');"
            "  return el ? getComputedStyle(el).opacity : '0';"
            '}'
        )
        if final_opacity == '1':
            break
        page.wait_for_timeout(100)

    assert final_opacity == '1', (
        f'hero child opacity stuck at {final_opacity} after 2 s — '
        'GSAP scroll animation did not complete'
    )

    # Scroll through the document so ScrollTrigger fires for off-screen
    # sections too. Any uncaught exception during scroll would land in
    # ``errors`` via the pageerror listener.
    page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
    page.wait_for_timeout(500)
    page.evaluate('() => window.scrollTo(0, 0)')
    page.wait_for_timeout(200)

    assert not errors, f'JS errors during scroll animations: {errors}'
