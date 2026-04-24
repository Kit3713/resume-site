"""
Client IP Resolution — Phase 23.2

Single source of truth for "which IP is the actual caller?" across every
code path that needs to make a trust decision about the remote end.

Historical context — Phase 22.6 (v0.3.1)
-----------------------------------------
The original admin route trusted ``X-Forwarded-For`` unconditionally.
Issue #16 of the 2026-04-18 audit identified this as spoofable on any
deployment where the container is also directly reachable (common for
Tailscale-fronted homelabs): an attacker who bypassed the reverse proxy
could forge ``X-Forwarded-For: 127.0.0.1`` and look like a local admin
to ``restrict_to_allowed_networks``. v0.3.1 Phase 22.6 fixed the admin
route only — the other four call sites (contact rate limit, API rate
limit, analytics, ``/metrics`` access control, login throttle hash)
still took the leftmost XFF entry blindly.

Phase 23.2 (this module) extracts the one correct algorithm and makes
every site call it. Issue #34 is closed when every inlined duplicate
is gone; the `test_no_inlined_xff_logic` grep-guard in
tests/test_security.py fails CI if a new one reappears.

The algorithm
-------------
A ``X-Forwarded-For: a, b, c`` header is appended-to by each hop along
the proxy chain. The RIGHTMOST entry (``c``) is whoever spoke to *us*
(and is therefore almost always one of our trusted proxies, if the
chain is honest); the LEFTMOST (``a``) is whatever the earliest hop
claimed about the original client.

An attacker controls the LEFTMOST entry — they can set whatever they
want before the first trusted proxy sees their request. They do NOT
control the RIGHTMOST entries added by trusted proxies downstream. So
the correct walk is:

1. The TCP peer (``request.remote_addr``) must itself be inside the
   ``trusted_proxies`` set. Otherwise the request came direct — there's
   no trust chain and XFF is ignored entirely.
2. Walk ``X-Forwarded-For`` RIGHT-TO-LEFT.
3. Return the first IP that is NOT in ``trusted_proxies``. That's the
   last untrusted hop — the real client.
4. Fall back to ``request.remote_addr`` if XFF is empty, malformed, or
   consists entirely of trusted proxies (which means the caller IS a
   trusted proxy talking to us directly).

Rationale (vs. "take the leftmost when peer is trusted", the v0.3.1
interim fix): the leftmost-when-peer-is-trusted algorithm still
accepts a forged XFF from one of our own trusted proxies, because the
proxy faithfully relays the forged entry from the attacker. The
right-to-left walk stops at the first IP that can't be trusted to have
filtered its own XFF, which is the real security boundary.
"""

from __future__ import annotations

import ipaddress

from flask import current_app

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_cidr_list(entries) -> list[_IPNetwork]:
    """Return a list of ``IPv4Network`` / ``IPv6Network`` from raw config.

    Accepts either the YAML-loaded list (``list[str]``), a comma-separated
    string, or ``None`` / empty. Malformed entries are silently skipped;
    the goal is defence-in-depth rather than strict validation, and
    config load already warns on unknown fields.
    """
    if not entries:
        return []
    if isinstance(entries, str):
        entries = [e.strip() for e in entries.split(',') if e.strip()]
    out: list[_IPNetwork] = []
    for entry in entries:
        try:
            out.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except (ValueError, TypeError):
            continue
    return out


def get_client_ip(request, trusted_proxies: list[_IPNetwork] | None = None) -> str:
    """Return the effective client IP for the current Flask ``request``.

    When ``trusted_proxies`` is not provided, the function reads the
    ``trusted_proxies`` entry from ``current_app.config['SITE_CONFIG']``
    so callers in route handlers don't have to thread the config through.
    Pass an explicit list to bypass the config read (handy in tests and
    code paths that already hold a parsed list).

    Returns the plain string form of the IP so callers that previously
    concatenated it into SQL strings, log lines, or hash inputs don't
    have to change their types.
    """
    if trusted_proxies is None:
        try:
            site_config = current_app.config.get('SITE_CONFIG', {}) or {}
        except RuntimeError:  # outside application context
            site_config = {}
        trusted_proxies = parse_cidr_list(site_config.get('trusted_proxies'))

    direct = request.remote_addr or ''
    xff = request.headers.get('X-Forwarded-For', '') or ''

    if not xff or not trusted_proxies:
        return direct

    try:
        direct_addr = ipaddress.ip_address(direct)
    except (ValueError, TypeError):
        return direct
    if not any(direct_addr in net for net in trusted_proxies):
        # TCP peer is not a trusted proxy — no trust chain, XFF ignored.
        return direct

    # Walk right-to-left. First untrusted IP wins.
    for entry in reversed([s.strip() for s in xff.split(',')]):
        if not entry:
            continue
        try:
            addr = ipaddress.ip_address(entry)
        except ValueError:
            continue
        if not any(addr in net for net in trusted_proxies):
            return entry

    # Every XFF entry is itself a trusted proxy — fall back to the peer.
    return direct
