"""
URL Construction Helpers — Phase 23.5 (#57)

A single place to build absolute URLs that land in SEO artifacts the
operator cannot easily edit after the fact:

* ``GET /sitemap.xml`` — consumed by search engines; a cached poison
  entry drives every bot to a wrong domain.
* ``GET /robots.txt`` — contains the sitemap URL.
* ``GET /blog/feed.xml`` — RSS readers cache the feed URL and every
  inside-feed ``<link>`` for the lifetime of the subscription, so a
  cached ``Host: attacker.example`` response drives subscribers to
  the wrong domain permanently.

Pre-23.5, every one of these was built from ``request.url_root``,
which reflects whatever ``Host:`` header the caller sent. A bot (or
attacker) could hit the origin directly with a spoofed Host header,
get a response naming the attacker's domain, and have that cached
downstream.

The fix: a new optional ``canonical_host`` config key pins the
operator-approved origin. When set, every URL-rooting callsite uses
it verbatim. When unset, the helpers fall back to ``request.url_root``
— identical to the pre-change behaviour, so existing deployments
without the key are not affected until they opt in.
"""

from __future__ import annotations

from flask import current_app, request


def canonical_url_root() -> str:
    """Return the canonical site URL root with a trailing slash.

    Examples (config) → return:
        ``canonical_host: "https://example.com"`` → ``"https://example.com/"``
        ``canonical_host: "https://example.com/"`` → ``"https://example.com/"``
        (unset)                                    → ``request.url_root``

    The trailing slash is preserved to match ``request.url_root``'s
    shape so callers can keep their ``.rstrip('/')`` idiom without
    having to branch on the config state.
    """
    try:
        site_config = current_app.config.get('SITE_CONFIG', {}) or {}
    except RuntimeError:
        # Outside application context — defer entirely to request.url_root
        # which Flask's test client synthesises from the TestClient host.
        return request.url_root

    canonical = (site_config.get('canonical_host') or '').strip()
    if not canonical:
        return request.url_root

    if not canonical.endswith('/'):
        canonical += '/'
    return canonical
