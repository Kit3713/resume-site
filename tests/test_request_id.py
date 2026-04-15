"""
Request ID Propagation Tests — Phase 18.1

Verifies the X-Request-ID correlation mechanism:
- Every response carries an X-Request-ID header.
- Auto-generated IDs are UUID4 hex (32 chars, no dashes).
- A safe inbound X-Request-ID is propagated verbatim.
- An unsafe inbound X-Request-ID is ignored and a new one is generated
  (defence against log-injection and header-smuggling attacks).
- `g.request_id` is populated before downstream handlers run.
"""

import re

from flask import g

from app import _REQUEST_ID_PATTERN, _assign_request_id

UUID4_HEX_RE = re.compile(r'^[0-9a-f]{32}$')


# ============================================================
# _REQUEST_ID_PATTERN — inbound header validation
# ============================================================


def test_pattern_accepts_uuid4_hex():
    assert _REQUEST_ID_PATTERN.match('a' * 32)
    assert _REQUEST_ID_PATTERN.match('0123456789abcdef0123456789abcdef')


def test_pattern_accepts_uuid4_with_dashes():
    assert _REQUEST_ID_PATTERN.match('550e8400-e29b-41d4-a716-446655440000')


def test_pattern_accepts_dot_and_underscore():
    assert _REQUEST_ID_PATTERN.match('req.12345_abcdef')


def test_pattern_rejects_too_short():
    assert _REQUEST_ID_PATTERN.match('abc123') is None


def test_pattern_rejects_too_long():
    assert _REQUEST_ID_PATTERN.match('a' * 129) is None


def test_pattern_rejects_empty():
    assert _REQUEST_ID_PATTERN.match('') is None


def test_pattern_rejects_crlf_injection():
    assert _REQUEST_ID_PATTERN.match('abcdefgh\r\nSet-Cookie: evil=1') is None


def test_pattern_rejects_space():
    assert _REQUEST_ID_PATTERN.match('abc defghij') is None


def test_pattern_rejects_quote():
    assert _REQUEST_ID_PATTERN.match('abc"defghij') is None


def test_pattern_rejects_control_char():
    assert _REQUEST_ID_PATTERN.match('abcdefgh\x00ijklmnop') is None


def test_pattern_rejects_unicode():
    assert _REQUEST_ID_PATTERN.match('abcdefgh\u00e9ijklmnop') is None


# ============================================================
# End-to-end propagation via the Flask test client
# ============================================================


def test_response_always_has_request_id(client):
    """Every public response carries an X-Request-ID header."""
    response = client.get('/')
    assert 'X-Request-ID' in response.headers


def test_auto_generated_id_is_uuid4_hex(client):
    """When the client doesn't send one, we mint a UUID4 hex."""
    response = client.get('/')
    assigned = response.headers.get('X-Request-ID', '')
    assert UUID4_HEX_RE.match(assigned), f'expected UUID4 hex, got {assigned!r}'


def test_safe_inbound_header_is_propagated(client):
    """A well-formed inbound X-Request-ID must be echoed back verbatim."""
    inbound = 'trace-abc123.xyz_0987654321'
    response = client.get('/', headers={'X-Request-ID': inbound})
    assert response.headers.get('X-Request-ID') == inbound


def test_malformed_inbound_header_is_replaced(client):
    """A transport-legal but pattern-invalid inbound ID must not be echoed.

    Werkzeug's test client refuses to send headers containing CR/LF, so
    that attack surface is covered at the transport layer. This test
    exercises the application-level defence: a header that *does* reach
    Flask but fails our allowlist (here, whitespace and a quote) must be
    replaced with a fresh UUID rather than passed through.
    """
    sneaky = 'badpayload "; attacker-data'
    response = client.get('/', headers={'X-Request-ID': sneaky})
    assigned = response.headers.get('X-Request-ID', '')
    assert assigned != sneaky
    assert ' ' not in assigned
    assert '"' not in assigned
    assert UUID4_HEX_RE.match(assigned)


def test_short_inbound_header_is_replaced(client):
    """Inbound IDs below the minimum length fall back to an auto-generated one."""
    response = client.get('/', headers={'X-Request-ID': 'short'})
    assigned = response.headers.get('X-Request-ID', '')
    assert assigned != 'short'
    assert UUID4_HEX_RE.match(assigned)


def test_different_requests_get_different_ids(client):
    """Consecutive requests without inbound IDs receive distinct values."""
    a = client.get('/').headers.get('X-Request-ID')
    b = client.get('/').headers.get('X-Request-ID')
    assert a and b
    assert a != b


def test_header_present_on_404(client):
    """The correlation header must appear even on error responses."""
    response = client.get('/does-not-exist')
    assert response.status_code == 404
    assert UUID4_HEX_RE.match(response.headers.get('X-Request-ID', ''))


def test_header_present_on_admin(client):
    """Admin routes also propagate the correlation header."""
    response = client.get('/admin/login')
    assert 'X-Request-ID' in response.headers


# ============================================================
# Unit test: g.request_id is set before downstream handlers
# ============================================================


def test_assign_request_id_populates_g(app):
    """Calling _assign_request_id directly must set flask.g.request_id."""
    with app.test_request_context('/', headers={'X-Request-ID': 'trace.abc123.xyz_999'}):
        _assign_request_id()
        assert g.request_id == 'trace.abc123.xyz_999'


def test_assign_request_id_generates_when_missing(app):
    """No inbound header → freshly generated UUID4 hex."""
    with app.test_request_context('/'):
        _assign_request_id()
        assert UUID4_HEX_RE.match(g.request_id)


def test_assign_request_id_rejects_bad_incoming(app):
    """Malformed inbound header → new ID generated, attack payload discarded."""
    with app.test_request_context('/', headers={'X-Request-ID': 'x'}):
        _assign_request_id()
        assert g.request_id != 'x'
        assert UUID4_HEX_RE.match(g.request_id)
