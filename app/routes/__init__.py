"""Flask Blueprint package.

Each submodule registers one Blueprint covering a logical slice of the
site: ``public`` (marketing pages + blog), ``admin`` (password-gated CMS),
``contact`` (form submission), ``review`` (testimonial submission +
moderation), plus the narrow ``health`` / ``metrics`` endpoints.

Blueprints are wired into the application in :func:`app.create_app`;
nothing in this package is importable at module level.
"""
