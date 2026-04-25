"""
WAF-Lite Request Filter — Phase 13.3

Lightweight before-request handler that inspects incoming requests for
common attack patterns. This is NOT a full WAF — it catches low-hanging
probes (path traversal, obvious SQL injection fingerprints, oversized
bodies, empty user-agents) and returns 400 to reduce noise in the logs.

Configurable via two settings:
- ``request_filter_enabled`` (default ``true``) — master toggle.
- ``request_filter_log_only`` (default ``false``) — when true, log
  violations at WARNING but don't block the request (tuning mode).

Returns 400 (not 403) on block so the filter's existence isn't revealed
to scanners.

v0.3.3 closes four bypass classes (#84, #85, #88, #136):

- #84 — SQLi regex previously inspected ``request.query_string`` only,
  so POST/PUT/PATCH bodies (form-encoded or JSON) bypassed the regex.
  Now decodes and scans the first 64 KB of the body too. The 64 KB cap
  is well above any realistic SQLi payload (RFC 9110 puts typical
  *header* limits at ~8 KB; legitimate SQLi probes fit comfortably
  under that) and well below any blob upload, so the filter stays
  cheap on photo POSTs.

- #85 — Requests with ``Transfer-Encoding: chunked`` and no
  ``Content-Length`` skipped both the size gate and the missing-CT
  gate. Chunked requests now force-read the body (Werkzeug enforces
  ``MAX_CONTENT_LENGTH`` and aborts 413 itself when exceeded) and
  participate in the missing-CT gate.

- #88 — The path-traversal regex matched ``request.path`` (already
  partially decoded by Werkzeug) and ``RAW_URI`` (gunicorn-only).
  Probes like ``%2e%2e%2f`` only matched on gunicorn; double-encoded
  ``%252e%252e`` slipped past everywhere. Now URL-decode the raw
  path in a loop (cap 5 iterations) before regex matching so single-
  and double-encoded forms collapse to the literal sequence.

- #136 — The path-traversal regex was byte-for-byte ASCII (``\\.\\.``).
  Unicode lookalikes (full-width ``．．`` U+FF0E and ``／`` U+FF0F)
  bypassed it. The decoded path is now NFKC-normalised (which folds
  full-width forms to their ASCII equivalents) and bidi-override
  characters (U+202E, U+2066-U+2069) are stripped before regex.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import unquote

from flask import abort, request

_log = logging.getLogger('app.security')

# Body-inspection cap for SQLi scanning (#84). 64 KB is two orders of
# magnitude above a realistic SQLi payload but below any photo upload,
# keeping the regex cheap on legitimate POST bodies.
_BODY_INSPECT_CAP = 64 * 1024

# Decode iteration cap for URL-decoded path traversal (#88). One
# unquote handles ``%2e``; double-encoded ``%252e`` needs two. Five is
# a safe ceiling against pathological inputs (``%25...%25``).
_UNQUOTE_MAX_ITER = 5

# Bidi-override characters (#136). Stripped before regex so an
# attacker can't smuggle visual ``..`` past the filter using RTL
# overrides.
_BIDI_OVERRIDES = re.compile(r'[‮⁦-⁩]')

_PATH_TRAVERSAL = re.compile(r'(\.\./|\.\.\\|%00|\x00)', re.IGNORECASE)

_SQL_INJECTION = re.compile(
    r"('.*(\bOR\b|\bAND\b).*[=<>]|"
    r'\bUNION\b.*\bSELECT\b|'
    r';\s*(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE)\b)',
    re.IGNORECASE,
)


def check_request(settings: dict) -> None:
    """Inspect the current request and abort(400) on suspicious patterns.

    Args:
        settings: The cached settings dict (from ``get_all_cached``).
    """
    if str(settings.get('request_filter_enabled', 'true')).lower() not in {
        '1',
        'true',
        'yes',
        'on',
    }:
        return

    log_only = str(settings.get('request_filter_log_only', 'false')).lower() in {
        '1',
        'true',
        'yes',
        'on',
    }

    reason = _detect_violation()
    if reason:
        _log.warning(
            'request filter: reason=%s method=%s path=%s ip=%s ua=%s',
            reason,
            request.method,
            request.path,
            request.remote_addr,
            (request.user_agent.string or '-')[:200],
        )
        if not log_only:
            abort(400)


def _normalise_path(path: str) -> str:
    """Collapse URL- and Unicode-encoded variants to their literal form.

    Closes #88 (URL-encoded ``%2e%2e``, double-encoded ``%252e``) and
    #136 (full-width ``．．``, bidi overrides) by:

    1. Iteratively unquoting until the string stops changing (cap 5).
    2. NFKC-normalising so full-width forms fold to ASCII.
    3. Stripping bidi-override characters that could mask traversal.
    """
    decoded = path
    for _ in range(_UNQUOTE_MAX_ITER):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    decoded = unicodedata.normalize('NFKC', decoded)
    decoded = _BIDI_OVERRIDES.sub('', decoded)
    return decoded


def _detect_violation() -> str | None:
    """Return a short reason string if the request looks suspicious, else None."""
    # Path traversal (#88, #136): normalise the raw URI through unquote
    # + NFKC + bidi-strip so URL-encoded, double-encoded, and Unicode
    # lookalike variants all collapse to the literal ``..``.
    raw_path = request.environ.get('RAW_URI', '') or request.full_path
    candidates = {
        _normalise_path(request.path),
        _normalise_path(raw_path),
    }
    for p in candidates:
        if _PATH_TRAVERSAL.search(p):
            return 'path_traversal'

    query = request.query_string.decode('utf-8', errors='replace')
    if query and _SQL_INJECTION.search(query):
        return 'sql_injection_probe'

    # Body-aware SQLi scan (#84) and chunked-transfer enforcement (#85).
    # Body inspection only runs on body-bearing methods. RFC 7230 §3.3.3
    # makes Transfer-Encoding authoritative when both are present, so
    # any ``chunked`` header is treated as size-unknown — force-read the
    # body, then re-check size against ``MAX_CONTENT_LENGTH``.
    if request.method in ('POST', 'PUT', 'PATCH'):
        te = (request.headers.get('Transfer-Encoding') or '').lower()
        is_chunked = 'chunked' in te

        # Eager body read: a chunked request otherwise streams past the
        # size gate. ``cache=True`` so view code can re-read the same
        # bytes. Werkzeug's ``LimitedStream`` raises ``RequestEntityTooLarge``
        # (413) the moment we read past ``MAX_CONTENT_LENGTH`` — propagate
        # it so the client sees the right status.
        if is_chunked:
            request.get_data(parse_form_data=False, cache=True)

        # Missing-Content-Type gate: the original check ignored chunked
        # requests because they have no Content-Length. Include them.
        ct = request.content_type or ''
        if not ct and ((request.content_length and request.content_length > 0) or is_chunked):
            return 'missing_content_type'

        # SQLi body scan: cap at 64 KB to keep the regex cheap.
        body = request.get_data(parse_form_data=False, cache=True)
        if body:
            sample = body[:_BODY_INSPECT_CAP].decode('utf-8', errors='ignore')
            if _SQL_INJECTION.search(sample):
                return 'sql_injection_probe'

        # Oversized chunked body (#85): when Werkzeug truncates a
        # chunked stream at exactly ``MAX_CONTENT_LENGTH``, the cached
        # body length equals the cap. Probe the stream for one more
        # byte; ``LimitedStream`` raises 413 on the over-read, which
        # bubbles up to Flask's error handler and yields the correct
        # response status.
        max_body = _max_content_length()
        if is_chunked and max_body and body and len(body) >= max_body:
            try:
                # ``request.stream`` is exhausted after get_data, but
                # the underlying ``wsgi.input`` may still hold bytes.
                extra = request.environ['wsgi.input'].read(1)
            except Exception:  # noqa: BLE001 — tolerate any stream weirdness
                extra = b''
            if extra:
                # Real chunked overflow: surface 413 explicitly.
                abort(413)

    if request.content_length and request.content_length > 10 * 1024 * 1024:
        return 'oversized_body'

    return None


def _max_content_length() -> int | None:
    """Return ``MAX_CONTENT_LENGTH`` from the active Flask app config.

    Returns ``None`` outside an app context.
    """
    from flask import current_app

    try:
        return current_app.config.get('MAX_CONTENT_LENGTH')
    except RuntimeError:
        return None
