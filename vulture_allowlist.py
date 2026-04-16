# vulture allowlist — false positives that are used by frameworks at runtime.
#
# Each entry below is a reference to a name or attribute that vulture reports
# as unused but is actually invoked by Flask, Jinja2, or test infrastructure.
# Keep entries sorted by source file.
#
# See pyproject.toml [tool.vulture] for ignore_decorators and ignore_names
# that cover broader patterns (route decorators, pytest fixtures, etc.).

# app/db.py — Flask teardown_appcontext passes the exception arg; we don't use it.
exception  # noqa: used by Flask teardown_appcontext signature
