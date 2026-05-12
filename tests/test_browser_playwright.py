"""Playwright browser tests (Phase 31).

These tests run only when RUN_PLAYWRIGHT_TESTS=1.
"""

from __future__ import annotations

import os
import re

import pytest

BASE_URL = os.getenv('PLAYWRIGHT_BASE_URL', 'http://localhost:8080')
ADMIN_USER = os.getenv('PLAYWRIGHT_ADMIN_USER', 'admin')
ADMIN_PASS = os.getenv('PLAYWRIGHT_ADMIN_PASS', 'admin')

pytestmark = pytest.mark.skipif(
    os.getenv('RUN_PLAYWRIGHT_TESTS') != '1',
    reason='Set RUN_PLAYWRIGHT_TESTS=1 for Playwright browser tests.',
)


def _new_page(p):
    browser = p.chromium.launch()
    page = browser.new_page()
    errors: list[str] = []
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    return browser, page, errors


def _admin_login(page):
    page.goto(f'{BASE_URL}/admin/login', wait_until='domcontentloaded')
    page.fill('#username', ADMIN_USER)
    page.fill('#password', ADMIN_PASS)
    page.click('button[type="submit"]')


def test_theme_toggle_persists_on_reload_and_css_var():
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser, page, errors = _new_page(p)
        page.goto(f'{BASE_URL}/', wait_until='domcontentloaded')
        page.evaluate("localStorage.setItem('theme', 'light')")
        page.reload(wait_until='domcontentloaded')

        assert page.locator('html').get_attribute('data-theme') == 'light'
        bg = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()")
        assert bg
        assert not errors
        browser.close()


def test_homepage_scroll_and_no_console_errors():
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser, page, errors = _new_page(p)
        page.goto(f'{BASE_URL}/', wait_until='domcontentloaded')
        for section_id in ['hero', 'about', 'services', 'contact']:
            locator = page.locator(f'#{section_id}')
            if locator.count() > 0:
                locator.scroll_into_view_if_needed()
        assert not errors
        browser.close()


def test_csp_nonce_on_inline_scripts_public_and_admin_login():
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser, page, errors = _new_page(p)
        for path in ['/', '/admin/login']:
            page.goto(f'{BASE_URL}{path}', wait_until='domcontentloaded')
            scripts = page.locator('script:not([src])')
            for i in range(scripts.count()):
                nonce = scripts.nth(i).get_attribute('nonce')
                assert nonce and re.match(r'^[A-Za-z0-9+/=_-]+$', nonce)
        assert not errors
        browser.close()


def test_cdn_blocking_does_not_break_basic_render():
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.route('**cdnjs.cloudflare.com/**', lambda route: route.abort())
        page = context.new_page()
        page.goto(f'{BASE_URL}/', wait_until='domcontentloaded')
        assert page.locator('body').count() == 1
        assert '500' not in page.title()
        browser.close()


def test_theme_editor_live_preview_updates_iframe():
    pw = pytest.importorskip('playwright.sync_api')

    with pw.sync_playwright() as p:
        browser, page, _ = _new_page(p)
        _admin_login(page)
        page.goto(f'{BASE_URL}/admin/theme', wait_until='domcontentloaded')
        if page.locator('#accent-color').count() == 0:
            pytest.skip('Theme editor unavailable (login may not be configured in CI).')

        page.fill('#accent-hex', '#ff0000')
        page.dispatch_event('#accent-hex', 'change')
        frame = page.frame_locator('#preview-frame')
        val = frame.locator('html').evaluate(
            "el => getComputedStyle(el).getPropertyValue('--color-accent').trim()"
        )
        assert val
        browser.close()
