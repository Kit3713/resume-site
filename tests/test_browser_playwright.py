"""Playwright browser smoke tests (Phase 31 seed).

These tests intentionally cover one high-value workflow first:
- theme persistence via localStorage + reload.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv('RUN_PLAYWRIGHT_TESTS') != '1',
    reason='Set RUN_PLAYWRIGHT_TESTS=1 for Playwright browser tests.',
)


def test_theme_toggle_persists_on_reload():
    """Set theme to light in localStorage and verify it survives reload."""
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto('http://localhost:8080/', wait_until='domcontentloaded')

        page.evaluate("localStorage.setItem('theme', 'light')")
        page.reload(wait_until='domcontentloaded')

        theme = page.locator('html').get_attribute('data-theme')
        assert theme == 'light'

        browser.close()
