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

    Args:
        lang: ISO 639-1 language code (e.g., 'en', 'es', 'fr').
    """
    session['locale'] = lang
    # Redirect to the referring page, or home if no referrer
    return redirect(request.referrer or '/')
