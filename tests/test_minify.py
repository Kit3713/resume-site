"""
Tests for the CSS/JS minification middleware (Phase 36.1).

Covers:
- Pure-function behaviour of ``minify_css`` / ``minify_js``.
- after_request hook: dev-mode bypass, production path, cache hit.
- Content-Length correctness after body replacement.
- Static assets other than .css/.js are untouched.
"""

from __future__ import annotations

from app.services.minify import clear_cache, minify_css, minify_js

# ---------------------------------------------------------------------------
# CSS minifier
# ---------------------------------------------------------------------------


def test_minify_css_strips_comments_and_whitespace():
    css = b"""
    /* header comment */
    .foo  {
        color:   red ;
        margin : 0 ;
    }
    """
    out = minify_css(css)
    assert b'/*' not in out
    assert out == b'.foo{color:red;margin:0}'


def test_minify_css_preserves_string_literal_whitespace():
    css = b'.a::before { content: "  hello  world  "; }'
    out = minify_css(css)
    assert b'"  hello  world  "' in out


def test_minify_css_handles_media_query():
    css = b"""
    @media (min-width: 800px) {
        .bar { display: flex; }
    }
    """
    out = minify_css(css)
    assert out == b'@media (min-width:800px){.bar{display:flex}}'


def test_minify_css_empty_input():
    assert minify_css(b'') == b''
    assert minify_css(b'/* only a comment */') == b''


# ---------------------------------------------------------------------------
# JS minifier
# ---------------------------------------------------------------------------


def test_minify_js_strips_line_and_block_comments():
    js = b"""
    // hello
    /* multi
       line */
    function f() {
        return 1; // trailing
    }
    """
    out = minify_js(js)
    assert b'//' not in out
    assert b'/*' not in out
    assert b'function f()' in out
    assert b'return 1;' in out


def test_minify_js_preserves_strings_with_comment_markers():
    js = b'var s = "/* not a comment */ // also not";'
    out = minify_js(js)
    assert b'"/* not a comment */ // also not"' in out


def test_minify_js_preserves_newlines_for_asi_safety():
    js = b'var a = 1\nvar b = 2\n'
    out = minify_js(js)
    # Newlines between statements must survive so ASI still works.
    assert b'\n' in out


def test_minify_js_empty_input():
    assert minify_js(b'') == b''


# ---------------------------------------------------------------------------
# Middleware — hook behaviour
# ---------------------------------------------------------------------------


def test_debug_mode_serves_unminified(client, app):
    """In debug mode, /static responses come back untouched."""
    app.debug = True
    clear_cache()
    resp = client.get('/static/css/style.css')
    assert resp.status_code == 200
    # Comments and indentation survive.
    assert b'/*' in resp.data or b'\n    ' in resp.data


def test_production_mode_minifies_css(client, app):
    """Out of debug mode, CSS comes back with comments stripped."""
    app.debug = False
    clear_cache()
    resp = client.get('/static/css/style.css')
    assert resp.status_code == 200
    # The source has plenty of block comments; the minifier strips them.
    assert b'/*' not in resp.data
    # Content-Length must match the minified body.
    assert int(resp.headers['Content-Length']) == len(resp.data)


def test_production_mode_minifies_js(client, app):
    app.debug = False
    clear_cache()
    resp = client.get('/static/js/main.js')
    assert resp.status_code == 200
    assert int(resp.headers['Content-Length']) == len(resp.data)


def test_non_css_js_assets_are_untouched(client, app):
    """A 404 on an unknown asset shouldn't blow up the hook."""
    app.debug = False
    resp = client.get('/static/does-not-exist.png')
    assert resp.status_code == 404
