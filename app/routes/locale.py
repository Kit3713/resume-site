"""
Locale Routing Blueprint

Handles language switching and locale persistence. Provides a /set-locale
endpoint that stores the selected language in the session and redirects
back to the referring page.

The locale is resolved in the app factory's get_locale() function using
the priority: session > Accept-Language header > default.
"""

from flask import Blueprint, redirect, request, session

locale_bp = Blueprint('locale', __name__)


@locale_bp.route('/set-locale/<lang>')
def set_locale(lang):
    """Set the active locale in the session and redirect back.

    Phase 27.6 (#21, #40) — the redirect target is validated against
    the current request's origin so a crafted Referer header (or a
    referrer-policy chain from an attacker-controlled site) can't turn
    ``/set-locale/en`` into an open redirect to ``attacker.example``.
    Same-origin referrers go through; everything else redirects to /.

    Args:
        lang: ISO 639-1 language code (e.g., 'en', 'es', 'fr').
    """
    from urllib.parse import urlparse

    session['locale'] = lang

    referrer = request.referrer or ''
    if referrer:
        try:
            parsed = urlparse(referrer)
            current = urlparse(request.host_url)
            # Accept only same-origin redirects. Empty netloc means a
            # relative path — those are always same-origin. Otherwise
            # the scheme + netloc must match the current request's.
            same_origin = not parsed.netloc or (
                parsed.scheme == current.scheme and parsed.netloc == current.netloc
            )
        except ValueError:
            same_origin = False
        if same_origin:
            return redirect(referrer)

    return redirect('/')
