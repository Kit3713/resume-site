"""Phase 31 — Playwright fixtures.

Provides the shared fixtures every browser test uses:

* ``base_url`` — env-var driven (``BASE_URL``), default ``http://localhost:5000``.
  Lets CI / developer machines point the suite at any running server without
  editing source.
* ``admin_credentials`` — env-var driven, default ``admin`` / ``testpassword123``
  (the password the conftest in ``tests/`` hashes into its test config).
* ``admin_storage_state`` — session-scoped: logs in ONCE and caches the
  resulting browser storage state (cookies + localStorage) in a tmpdir file.
  Every test that needs auth replays this state, so the suite as a whole
  triggers the admin login rate-limiter exactly once.
* ``authenticated_page`` — function-scoped Playwright ``Page`` whose context
  loads the cached storage state. The page lands already logged-in without
  re-driving the login form.

The ``page`` fixture comes from pytest-playwright itself; we layer on top.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect


@pytest.fixture(scope='session')
def base_url() -> str:
    """Return the base URL of the running app under test.

    Reads ``BASE_URL`` from the environment so CI workflows can point at a
    container or alternate port without code edits. Defaults to the dev
    server's standard bind.
    """
    return os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')


@pytest.fixture(scope='session')
def admin_credentials() -> dict[str, str]:
    """Return the admin login credentials the test suite uses.

    Defaults match the password hash baked into ``tests/conftest.py`` — when
    the server is started with that config the values below log in. Override
    via ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` for CI or alternate seeds.
    """
    return {
        'username': os.environ.get('ADMIN_USERNAME', 'admin'),
        'password': os.environ.get('ADMIN_PASSWORD', 'testpassword123'),
    }


@pytest.fixture(scope='session')
def admin_storage_state(
    browser: Browser,
    base_url: str,
    admin_credentials: dict[str, str],
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Log in once per session; cache the resulting cookies + localStorage.

    Flask-Limiter caps ``/admin/login`` POSTs at 5 per minute per IP. With
    nine browser tests each driving the form fresh we'd exhaust the budget
    in under a minute. Drive login once at session start, dump the storage
    state to a JSON file, and let every ``authenticated_page`` rebuild a
    context from that cache.

    Returns the path to the storage-state JSON; downstream fixtures pass
    it to ``browser.new_context(storage_state=...)``.
    """
    state_path = tmp_path_factory.mktemp('playwright_auth') / 'admin_state.json'

    context = browser.new_context()
    page = context.new_page()
    page.goto(f'{base_url}/admin/login')
    page.fill('input[name="username"]', admin_credentials['username'])
    page.fill('input[name="password"]', admin_credentials['password'])
    page.click('button[type="submit"]')
    # Successful login redirects to /admin/. If we instead stay on
    # /admin/login the credentials were wrong — fail fast with a clear
    # assertion so every downstream test reports the same root cause.
    expect(page).not_to_have_url(f'{base_url}/admin/login', timeout=5000)

    context.storage_state(path=str(state_path))
    context.close()
    return state_path


@pytest.fixture
def authenticated_page(
    browser: Browser,
    admin_storage_state: Path,
    base_url: str,
) -> Iterator[Page]:
    """Yield a Playwright page logged in as admin (via cached storage state).

    Building a fresh ``BrowserContext`` per test isolates cookies / local
    storage between tests while reusing the once-per-session login. The
    ``storage_state`` parameter pre-seeds the new context with the Flask
    session cookie captured by ``admin_storage_state``.
    """
    context = browser.new_context(storage_state=str(admin_storage_state))
    page = context.new_page()
    # Land on the admin dashboard so any test that doesn't explicitly
    # navigate still starts in admin scope.
    page.goto(f'{base_url}/admin/')
    expect(page).not_to_have_url(f'{base_url}/admin/login', timeout=5000)
    yield page
    context.close()
