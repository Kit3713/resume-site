"""Phase 31 — CSP enforcement: every inline <script> carries a nonce.

For every visited public + admin page, asserts that:

1. Every inline ``<script>`` element in the rendered DOM has a non-empty
   ``nonce`` attribute (i.e. wasn't accidentally template-stripped).
2. The response ``Content-Security-Policy`` header does NOT contain
   ``'unsafe-inline'`` — the nonce-based CSP is the only auth scheme
   for inline scripts.
3. The nonce in every ``<script>`` matches the one declared in the CSP
   header (script-src 'nonce-…').

Covers the v0.3.0 Phase 13.2 carry-over that was parked pending Playwright.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page

# Pages to probe. Admin pages are visited under an authenticated context;
# public pages can be visited unauthenticated. Each tuple is (path, requires_auth).
_PAGES: list[tuple[str, bool]] = [
    ('/', False),
    ('/services', False),
    ('/portfolio', False),
    ('/projects', False),
    ('/testimonials', False),
    ('/certifications', False),
    ('/contact', False),
    ('/admin/login', False),
    ('/admin/', True),
    ('/admin/photos', True),
    ('/admin/services', True),
    ('/admin/theme', True),
    ('/admin/content', True),
    ('/admin/settings', True),
]

_NONCE_RE = re.compile(r"'nonce-([^']+)'")


def _assert_csp_contract(page: Page, response: Any, path: str) -> None:
    """Run the three-part CSP contract on the just-loaded page.

    ``response`` is duck-typed (it's a ``playwright.sync_api.Response``
    in practice; we just need ``.headers``) so this helper stays
    independent of Playwright's private response-type re-exports.
    """
    csp = response.headers.get('content-security-policy', '')
    assert csp, f'{path}: missing Content-Security-Policy header'
    assert 'unsafe-inline' not in csp, f"{path}: CSP contains 'unsafe-inline' — nonce mode required"

    # Pull the nonce the server claimed in the header.
    match = _NONCE_RE.search(csp)
    assert match, f'{path}: CSP header lacks a nonce-* token: {csp}'
    header_nonce = match.group(1)

    # Every inline <script> in the rendered DOM must carry the same
    # nonce. External scripts (src=...) are allowed to omit nonce; the
    # CSP whitelists the CDN hostnames separately.
    #
    # Read ``el.nonce`` (the IDL attribute) rather than
    # ``getAttribute('nonce')``. Modern browsers (Chrome 76+) hide the
    # content attribute after CSP enforcement to prevent CSS exfiltration
    # — ``getAttribute('nonce')`` returns the empty string even though
    # the nonce is still valid and the script executed. The IDL property
    # exposes the live value to scripts running in the same origin.
    inline_nonces: list[str | None] = page.eval_on_selector_all(
        'script:not([src])',
        'els => els.map(e => e.nonce)',
    )
    for n in inline_nonces:
        assert n, f'{path}: an inline <script> is missing its nonce attribute'
        assert n == header_nonce, (
            f'{path}: inline script nonce {n!r} does not match header nonce {header_nonce!r}'
        )


@pytest.mark.parametrize(
    'path,requires_auth',
    [pytest.param(p, a, id=p) for p, a in _PAGES],
)
def test_csp_nonce_on_every_page(
    page: Page,
    authenticated_page: Page,
    base_url: str,
    path: str,
    requires_auth: bool,
) -> None:
    """Assert nonce contract holds for the given page."""
    target = authenticated_page if requires_auth else page
    response = target.goto(f'{base_url}{path}')
    assert response is not None, f'{path}: navigation produced no response'
    # 200 OK or a benign 302 from /admin/ root → /admin/dashboard are
    # both acceptable; anything else is a probe regression we'd rather
    # surface than ignore.
    assert response.status in (200, 302), f'{path}: unexpected status {response.status}'
    _assert_csp_contract(target, response, path)
