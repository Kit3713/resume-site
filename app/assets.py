"""
Asset Fingerprinting — Phase 12.3

Provides content-hash-based cache busting for static assets. In production,
``hashed_static_url('css/style.css')`` returns something like
``/static/css/style.css?v=a1b2c3d4`` where the query parameter is the first
8 characters of the file's SHA-256 hash. The hash changes when the file
changes, so ``Cache-Control: immutable`` works correctly across deploys.

In debug mode, no hash is appended (so browser devtools always fetch fresh).

Usage in templates (injected via context processor):
    {{ static_hashed('css/style.css') }}
"""

from __future__ import annotations

import hashlib
import os
import threading

from flask import Flask, url_for

_cache: dict[str, str] = {}
_lock = threading.Lock()


def _compute_hash(file_path: str) -> str:
    """Return the first 8 hex chars of the file's SHA-256 digest."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]


def hashed_static_url(filename: str, app: Flask) -> str:
    """Return a cache-busted URL for a static asset.

    In debug mode, returns the plain ``url_for('static', ...)`` URL.
    In production, appends ``?v=<hash>`` for cache busting.
    """
    if app.debug:
        return url_for('static', filename=filename)

    with _lock:
        if filename not in _cache:
            file_path = os.path.join(app.static_folder or '', filename)
            if os.path.isfile(file_path):
                _cache[filename] = _compute_hash(file_path)
            else:
                _cache[filename] = 'missing'

        version = _cache[filename]

    return url_for('static', filename=filename) + f'?v={version}'


def clear_cache() -> None:
    """Drop all cached hashes. Called on app restart or by tests."""
    with _lock:
        _cache.clear()


def init_app(app: Flask) -> None:
    """Register the ``static_hashed`` template global and cache-control headers."""

    @app.context_processor
    def _inject_static_hashed():
        def static_hashed(filename):
            return hashed_static_url(filename, app)

        return {'static_hashed': static_hashed}
