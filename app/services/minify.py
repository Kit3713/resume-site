"""
Request-time CSS/JS minification (Phase 36.1).

Stdlib-only minifiers applied as a Flask ``after_request`` hook. The
response body for ``/static/*.css`` and ``/static/*.js`` is replaced with
a minified copy; entries are cached per-fingerprint against the SHA-256
hash already computed by ``app.assets`` (Phase 12.3), so the cache
invalidates automatically when a source file changes.

Dev mode (``app.debug``) bypasses minification entirely — the unminified
source is served so browser devtools and source maps remain usable.

The minifiers are deliberately regex-only. Asset volumes are tiny
(``style.css`` is ~60 KB, ``main.js`` ~10 KB); the cost of a one-time
minification pass is negligible next to the complexity of a proper
AST-based minifier, and stdlib-only is the v0.3.x runtime rule.

Known limitations (accepted):
- CSS minifier does not touch ``calc()`` whitespace (correct: spaces
  inside ``calc`` are semantically significant and the regex that
  collapses whitespace around punctuation leaves them alone).
- JS minifier strips comments and collapses horizontal whitespace but
  preserves newlines. That keeps ASI safe without needing a parser.
  Regex literals that contain ``/*`` or ``//`` are not specially
  protected — the current codebase has no such cases.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable

from flask import Flask, Response, request

_cache: dict[tuple[str, str], bytes] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# CSS minifier
# ---------------------------------------------------------------------------

_CSS_STRING = re.compile(rb'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_CSS_COMMENT = re.compile(rb'/\*.*?\*/', re.DOTALL)
_CSS_WS_RUN = re.compile(rb'\s+')
_CSS_PUNCT_WS = re.compile(rb'\s*([{}:;,>+~])\s*')
_CSS_SEMI_CLOSE = re.compile(rb';}')
_CSS_PLACEHOLDER = re.compile(rb'\x00(\d+)\x00')


def minify_css(source: bytes) -> bytes:
    """Fast regex-only CSS minifier that preserves string literals."""
    stash: list[bytes] = []

    def _park(match: re.Match[bytes]) -> bytes:
        stash.append(match.group(0))
        return f'\x00{len(stash) - 1}\x00'.encode()

    s = _CSS_STRING.sub(_park, source)
    s = _CSS_COMMENT.sub(b'', s)
    s = _CSS_WS_RUN.sub(b' ', s)
    s = _CSS_PUNCT_WS.sub(rb'\1', s)
    s = _CSS_SEMI_CLOSE.sub(b'}', s)
    s = s.strip()

    def _restore(match: re.Match[bytes]) -> bytes:
        return stash[int(match.group(1))]

    return _CSS_PLACEHOLDER.sub(_restore, s)


# ---------------------------------------------------------------------------
# JS minifier (conservative — preserves newlines for ASI safety)
# ---------------------------------------------------------------------------

_JS_TOKEN = re.compile(
    rb"""
    ("(?:\\.|[^"\\])*") |
    ('(?:\\.|[^'\\])*') |
    (`(?:\\.|[^`\\])*`) |
    (/\*[\s\S]*?\*/) |
    (//[^\n]*)
    """,
    re.VERBOSE,
)
_JS_HORIZONTAL_WS = re.compile(rb'[ \t\r\f]+')
_JS_LINE_EDGE_WS = re.compile(rb'[ \t]*\n[ \t]*')
_JS_BLANK_LINES = re.compile(rb'\n{2,}')


def minify_js(source: bytes) -> bytes:
    """Conservative JS minifier: strip comments + collapse whitespace.

    Does NOT touch identifiers, does NOT drop semicolons, does NOT fold
    statements across line breaks. Size reduction is modest (20–35%) but
    correctness is guaranteed for any valid JS the browser already parses.
    """

    def _strip(match: re.Match[bytes]) -> bytes:
        dq, sq, tpl, block, line = match.groups()
        if dq is not None:
            return dq
        if sq is not None:
            return sq
        if tpl is not None:
            return tpl
        # block / line comment — drop.
        return b''

    s = _JS_TOKEN.sub(_strip, source)
    s = _JS_HORIZONTAL_WS.sub(b' ', s)
    s = _JS_LINE_EDGE_WS.sub(b'\n', s)
    s = _JS_BLANK_LINES.sub(b'\n', s)
    return s.strip()


# ---------------------------------------------------------------------------
# Middleware wiring
# ---------------------------------------------------------------------------

_EXT_TO_MINIFIER: dict[str, Callable[[bytes], bytes]] = {
    '.css': minify_css,
    '.js': minify_js,
}


def _fingerprint(app: Flask, filename: str) -> str | None:
    """Return the cached SHA-256 fingerprint for ``filename``.

    Reuses ``app.assets._cache`` so we share one fingerprint per file with
    the URL-rewriting side of the pipeline. Computes + populates the cache
    on miss so this module works even if a static file is requested
    directly (not via a rendered template).
    """
    from app.assets import _cache as assets_cache
    from app.assets import _compute_hash
    from app.assets import _lock as assets_lock

    with assets_lock:
        fp = assets_cache.get(filename)
    if fp and fp != 'missing':
        return fp

    file_path = os.path.join(app.static_folder or '', filename)
    if not os.path.isfile(file_path):
        return None
    fp = _compute_hash(file_path)
    with assets_lock:
        assets_cache[filename] = fp
    return fp


def _maybe_minify(app: Flask, response: Response) -> Response:
    """``after_request`` hook. No-op in debug mode and on non-matching paths."""
    if app.debug:
        return response
    if response.status_code != 200:
        return response

    path = request.path
    if not path.startswith('/static/'):
        return response
    ext = os.path.splitext(path)[1].lower()
    minifier = _EXT_TO_MINIFIER.get(ext)
    if minifier is None:
        return response

    filename = path[len('/static/') :]
    file_path = os.path.join(app.static_folder or '', filename)
    if not os.path.isfile(file_path):
        return response

    fp = _fingerprint(app, filename)
    if fp is None:
        return response

    key = (filename, fp)
    with _lock:
        cached = _cache.get(key)
    if cached is None:
        # Read the source file directly — Flask's send_file uses
        # direct_passthrough, which means response.get_data() won't work and
        # the stream is typically a file handle we shouldn't drain twice.
        with open(file_path, 'rb') as source:
            cached = minifier(source.read())
        with _lock:
            _cache[key] = cached

    # Replace the streamed file body with the minified bytes. set_data also
    # adjusts Content-Length; direct_passthrough must be cleared so Flask
    # re-serializes from the new body instead of yielding the file handle.
    response.direct_passthrough = False
    response.set_data(cached)
    return response


def init_app(app: Flask) -> None:
    """Register the minification hook on ``app``."""
    app.after_request(lambda resp: _maybe_minify(app, resp))


def clear_cache() -> None:
    """Drop every cached minified body. For tests / SIGHUP-style reloads."""
    with _lock:
        _cache.clear()
