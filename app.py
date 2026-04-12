"""
resume-site Application Entry Point

This module serves as the main entry point for both development and production.
- Development: Run directly with `python app.py` (debug mode on port 5000).
- Production: Gunicorn invokes `create_app()` via the Containerfile entrypoint.

The actual application logic lives in the `app` package; this file simply
bootstraps the Flask app instance using the factory pattern.
"""

from app import create_app

# Create the Flask application instance via the factory.
# Gunicorn references this as "app:create_app()" in its entrypoint.
app = create_app()

if __name__ == '__main__':
    # Local development server — not used in production.
    # Use `flask run --debug` as an alternative.
    app.run(debug=True, port=5000)
