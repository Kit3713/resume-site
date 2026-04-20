"""
resume-site Application Entry Point

This module serves as the main entry point for both development and production.
- Production: Gunicorn invokes ``create_app()`` via the Containerfile entrypoint
  — this script is never run directly in production.
- Development: ``python app.py`` boots a plain Werkzeug server. The Werkzeug
  interactive debugger (``/console``) is a remote-code-execution vector when
  exposed to anyone but the developer, so debug mode is *off* by default and
  must be opted in to with BOTH ``RESUME_SITE_DEV=1`` in the environment *and*
  the ``--debug`` CLI flag. Missing either gate keeps the debugger closed.

The actual application logic lives in the ``app`` package; this file simply
bootstraps the Flask app instance using the factory pattern.
"""

import argparse
import logging
import os
import sys

from app import create_app

# Create the Flask application instance via the factory.
# Gunicorn references this as "app:create_app()" in its entrypoint.
app = create_app()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='resume-site development server')
    parser.add_argument(
        '--debug',
        action='store_true',
        help=(
            'Enable the Werkzeug interactive debugger. Requires the environment '
            'variable RESUME_SITE_DEV=1 as a second gate so a stray `python app.py` '
            'never reopens the /console RCE vector. Never use in production.'
        ),
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Port to bind (default: 5000).',
    )
    args = parser.parse_args()

    debug_requested = args.debug
    dev_env = os.environ.get('RESUME_SITE_DEV') == '1'
    debug_enabled = debug_requested and dev_env

    if debug_requested and not dev_env:
        # Surface the refusal loudly so the developer notices the gate
        # instead of silently getting a non-debug server.
        print(
            'warning: --debug ignored because RESUME_SITE_DEV=1 is not set in the environment. '
            'Debug mode enables the Werkzeug interactive debugger (arbitrary code execution) '
            'and is fully gated behind both the env var and the flag.',
            file=sys.stderr,
        )

    if debug_enabled:
        logging.getLogger(__name__).warning(
            'Starting dev server with DEBUG=True — Werkzeug /console is an RCE surface. '
            'Bind to 127.0.0.1 only and never expose this process to an untrusted network.'
        )

    app.run(debug=debug_enabled, port=args.port)
