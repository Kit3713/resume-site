#!/usr/bin/env python3
"""
resume-site Management CLI

Provides command-line tools for server administration tasks that don't
require the admin panel (e.g., initial setup, headless management).

Commands:
    init-db              Initialize the SQLite database (runs all pending migrations).
    migrate              Apply pending database migrations.
    hash-password        Generate a pbkdf2:sha256 password hash for config.yaml.
    generate-secret      Generate a cryptographically secure secret_key value.
    generate-token       Create a review invitation token for a trusted contact.
    generate-api-token   Create an API token for programmatic access (Phase 13.4).
    rotate-api-token     Rotate a named API token (revokes the old one).
    revoke-api-token     Revoke an API token by id.
    list-api-tokens      List API tokens with status and metadata.
    list-reviews         Display reviews filtered by status (pending/approved/rejected).
    purge-analytics      Delete page view records older than N days.
    query-audit          Run EXPLAIN QUERY PLAN on documented hot queries.
    complexity-report    Print the top N most complex functions in the codebase.
    backup               Create, list, or prune timestamped site backups.
    restore              Restore the site database and photos from a backup.
    translations         Manage translation files (extract, init, compile, update).

Usage:
    python manage.py init-db
    python manage.py migrate
    python manage.py migrate --status
    python manage.py migrate --dry-run
    python manage.py hash-password
    python manage.py generate-secret
    python manage.py generate-token --name "John Doe" --type recommendation
    python manage.py generate-api-token --name "CI Bot" --scope read,write --expires 90d
    python manage.py rotate-api-token --name "CI Bot"
    python manage.py revoke-api-token --id 3
    python manage.py list-api-tokens
    python manage.py list-reviews --status pending
    python manage.py purge-analytics --days 90
    python manage.py query-audit
    python manage.py complexity-report
    python manage.py complexity-report --top 40
    python manage.py backup
    python manage.py backup --db-only
    python manage.py backup --list
    python manage.py backup --prune --keep 7
    python manage.py restore --from backups/resume-site-backup-20260401-120000.tar.gz --force
    python manage.py translations extract
    python manage.py translations init es
    python manage.py translations compile
    python manage.py translations update
"""

import argparse
import ast
import getpass
import os
import sqlite3
import sys
from datetime import UTC, datetime

from werkzeug.security import generate_password_hash


def _get_db_path():
    """Return the configured database path by loading the app config."""
    from app import create_app

    app = create_app()
    return app.config['DATABASE_PATH']


# Phase 21.2 extraction: the migration helpers moved to
# ``app.services.migrations`` so the readiness route can import them
# without dragging argparse + every CLI subcommand. The thin wrappers
# below preserve the old underscore-prefixed names so this module's
# internal call sites (and any external tooling that imported them)
# keep working with zero behaviour change.
from app.services.migrations import (  # noqa: E402 — top-of-file imports already loaded
    ensure_schema_version_table as _ensure_schema_version_table,
)
from app.services.migrations import (  # noqa: E402 — top-of-file imports already loaded
    get_applied_versions as _get_applied_versions,
)
from app.services.migrations import (  # noqa: E402 — top-of-file imports already loaded
    get_migrations_dir as _get_migrations_dir,
)
from app.services.migrations import (  # noqa: E402 — top-of-file imports already loaded
    list_migration_files as _list_migration_files,
)


def _detect_existing_db(conn):
    """Return True if this looks like a v0.1.0 database (settings table exists)."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    return 'settings' in tables


def _get_seeds_dir():
    """Return the path to the seeds/ directory."""
    return os.path.join(os.path.dirname(__file__), 'seeds')


def _run_seeds(db_path):
    """Run all seed SQL files to populate default data.

    Uses INSERT OR IGNORE so existing values are never overwritten.
    Safe to run multiple times.
    """
    seeds_dir = _get_seeds_dir()
    if not os.path.isdir(seeds_dir):
        return

    conn = sqlite3.connect(db_path)
    for fname in sorted(os.listdir(seeds_dir)):
        if fname.endswith('.sql'):
            path = os.path.join(seeds_dir, fname)
            with open(path) as f:
                conn.executescript(f.read())
            print(f'  Seeded: {fname}')
    conn.close()


def init_db(args):
    """Initialize the database by running all pending migrations and seeds.

    Delegates to the migrate command for schema, then runs seed SQL files
    for default data. Running init-db on an already-initialized database
    is safe — migrations skip applied versions, seeds use INSERT OR IGNORE.
    """

    # Reuse the migrate logic with no flags
    class _Args:
        status = False
        dry_run = False

    migrate(_Args())

    # Run seed data after migrations
    db_path = _get_db_path()
    _run_seeds(db_path)


def migrate(args):
    """Apply pending database migrations in order.

    Each migration is a numbered SQL file in migrations/. The schema_version
    table tracks which migrations have been applied. Existing v0.1.0 databases
    are detected by the presence of the settings table and are auto-marked as
    having migration 001 applied (since schema.sql created those tables).

    Flags:
        --status:   Print applied/pending status for all migrations and exit.
        --dry-run:  Print SQL that would be executed without making changes.
    """
    db_path = _get_db_path()
    migrations_dir = _get_migrations_dir()

    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    _ensure_schema_version_table(conn)
    applied = _get_applied_versions(conn)

    # Auto-detect existing v0.1.0 databases and mark baseline as applied
    if _detect_existing_db(conn) and 1 not in applied:
        conn.execute("INSERT INTO schema_version (version, name) VALUES (1, '001_baseline.sql')")
        conn.commit()
        applied.add(1)
        print('Detected existing database — marked 001_baseline as applied.')

    migration_files = _list_migration_files(migrations_dir)
    if not migration_files:
        print('No migration files found in migrations/')
        conn.close()
        return

    if args.status:
        print('Migration status:')
        for version, fname in migration_files:
            status = 'applied ' if version in applied else 'pending '
            print(f'  [{status}] {fname}')
        conn.close()
        return

    pending = [(v, f) for v, f in migration_files if v not in applied]
    if not pending:
        print('All migrations are already applied.')
        conn.close()
        return

    for version, fname in pending:
        path = os.path.join(migrations_dir, fname)
        with open(path) as f:
            sql = f.read()

        if args.dry_run:
            print(f'-- DRY RUN: {fname}')
            print(sql)
            print()
            continue

        try:
            conn.executescript(sql)
            conn.execute(
                'INSERT INTO schema_version (version, name) VALUES (?, ?)',
                (version, fname),
            )
            conn.commit()
            print(f'Applied: {fname}')
        except Exception as e:
            print(f'ERROR applying {fname}: {e}', file=sys.stderr)
            conn.close()
            sys.exit(1)

    conn.close()
    if not args.dry_run:
        print('Migrations complete.')


def config_validate(args):  # noqa: C901 — sequential validation of config fields; each branch is a distinct check, not tangled conditionals
    """Validate config.yaml against expected structure and values.

    Checks for:
    - Required fields (secret_key)
    - Unknown/unexpected keys (typos, settings-layer values in config)
    - Type correctness for known fields
    - Secret key strength
    - CIDR network format validity
    """
    import ipaddress

    from app.services.config import _WEAK_SECRET_KEYS

    _VALID_TOP_KEYS = {
        'secret_key',
        'database_path',
        'photo_storage',
        'max_upload_size',
        'session_timeout_minutes',
        'session_cookie_secure',
        'smtp',
        'admin',
    }
    _VALID_SMTP_KEYS = {'host', 'port', 'user', 'password', 'password_file', 'recipient'}
    _VALID_ADMIN_KEYS = {'username', 'password_hash', 'allowed_networks'}

    # Settings-layer keys that don't belong in config.yaml
    _SETTINGS_KEYS = {
        'site_title',
        'site_tagline',
        'dark_mode_default',
        'availability_status',
        'contact_form_enabled',
        'contact_email_visible',
        'contact_phone_visible',
        'contact_github_url',
        'contact_linkedin_url',
        'resume_visibility',
        'case_studies_enabled',
        'testimonial_display_mode',
        'analytics_retention_days',
        'hero_heading',
        'hero_subheading',
        'hero_tagline',
        'accent_color',
        'logo_mode',
        'footer_text',
    }

    config_path = os.environ.get(
        'RESUME_SITE_CONFIG',
        os.path.join(os.path.dirname(__file__), 'config.yaml'),
    )

    if not os.path.exists(config_path):
        print(f'ERROR: Config file not found: {config_path}', file=sys.stderr)
        sys.exit(1)

    import yaml as _yaml

    with open(config_path) as f:
        raw = _yaml.safe_load(f) or {}

    errors = []
    warnings = []

    # Check for unknown top-level keys
    for key in raw:
        if key in _SETTINGS_KEYS:
            warnings.append(
                f"'{key}' belongs in the admin settings panel, not config.yaml. "
                'It will be ignored here.'
            )
        elif key not in _VALID_TOP_KEYS:
            warnings.append(f"Unknown key '{key}' in config.yaml (possible typo?).")

    # Check required fields
    if 'secret_key' not in raw:
        errors.append("Required field 'secret_key' is missing.")
    else:
        sk = str(raw['secret_key'])
        if sk.lower() in _WEAK_SECRET_KEYS:
            warnings.append('secret_key is an example/placeholder value.')
        if len(sk) < 32:
            warnings.append(f'secret_key is only {len(sk)} chars (32+ recommended).')

    # Validate SMTP section
    smtp = raw.get('smtp', {})
    if isinstance(smtp, dict):
        for key in smtp:
            if key not in _VALID_SMTP_KEYS:
                warnings.append(f"Unknown key 'smtp.{key}' (possible typo?).")
        if 'port' in smtp and not isinstance(smtp['port'], int):
            errors.append('smtp.port must be an integer.')
    elif smtp is not None:
        errors.append("'smtp' must be a mapping (dict), not a scalar.")

    # Validate admin section
    admin = raw.get('admin', {})
    if isinstance(admin, dict):
        for key in admin:
            if key not in _VALID_ADMIN_KEYS:
                warnings.append(f"Unknown key 'admin.{key}' (possible typo?).")
        networks = admin.get('allowed_networks', [])
        if isinstance(networks, list):
            for net in networks:
                try:
                    ipaddress.ip_network(net, strict=False)
                except ValueError:
                    errors.append(f"Invalid CIDR network: '{net}'")
    elif admin is not None:
        errors.append("'admin' must be a mapping (dict), not a scalar.")

    # Validate integer fields
    for field in ('max_upload_size', 'session_timeout_minutes'):
        if field in raw and not isinstance(raw[field], int):
            errors.append(f"'{field}' must be an integer.")

    # Print results
    if errors:
        print('ERRORS:')
        for e in errors:
            print(f'  ✗ {e}')
    if warnings:
        print('WARNINGS:')
        for w in warnings:
            print(f'  ⚠ {w}')
    if not errors and not warnings:
        print('✓ config.yaml is valid.')
    elif errors:
        sys.exit(1)


def generate_secret(args):
    """Generate a cryptographically secure secret_key for config.yaml.

    Uses Python's secrets module to produce a 64-byte (512-bit) URL-safe
    random string. The output can be pasted directly into config.yaml.
    """
    import secrets as _secrets

    key = _secrets.token_urlsafe(64)
    print('\nPaste this into your config.yaml as secret_key:\n')
    print(f'  secret_key: "{key}"')


def rotate_secret_key(args):
    """Generate a new secret_key and write it into config.yaml.

    All active sessions will be invalidated — Flask session cookies are
    signed with the secret_key, so rotating it makes every existing
    cookie unverifiable. The admin will need to log in again.
    """
    import secrets as _secrets

    import yaml

    config_path = _config_path_for_backup()
    if not os.path.isfile(config_path):
        print(f'ERROR: config.yaml not found at {config_path}', file=sys.stderr)
        sys.exit(1)

    new_key = _secrets.token_urlsafe(64)

    with open(config_path) as f:
        raw = f.read()
        config = yaml.safe_load(raw) or {}

    old_key = config.get('secret_key', '')
    config['secret_key'] = new_key

    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print('\n=== Secret Key Rotated ===')
    print(f'Config:   {config_path}')
    print(f'Old key:  {old_key[:8]}... (truncated)')
    print(f'New key:  {new_key[:8]}... (truncated)')
    print('\nWARNING: All active sessions are now invalid.')
    print('The admin must log in again.\n')


def rebuild_search_index(args):
    """Rebuild the FTS5 search index from scratch.

    Clears and re-populates the search_index virtual table from all
    content sources: content_blocks, blog_posts, reviews, photos, services.
    """
    import sqlite3

    db_path = _get_db_path()
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute('DELETE FROM search_index')
    except Exception:
        print('search_index table not found — run migrations first.', file=sys.stderr)
        sys.exit(1)

    sources = [
        ('content_block', 'content_blocks', 'id', 'title', 'plain_text'),
        ('blog_post', 'blog_posts', 'id', 'title', "COALESCE(summary,'') || ' ' || COALESCE(content,'')"),
        ('review', 'reviews', 'id', 'reviewer_name', 'message'),
        ('photo', 'photos', 'id', 'title', "COALESCE(description,'') || ' ' || COALESCE(category,'')"),
        ('service', 'services', 'id', 'title', 'description'),
    ]

    total = 0
    for content_type, table, id_col, title_col, body_expr in sources:
        rows = db.execute(f'SELECT {id_col}, {title_col}, {body_expr} AS body FROM {table}').fetchall()  # noqa: S608 — hardcoded table/column names
        for row in rows:
            db.execute(
                'INSERT INTO search_index(content_type, content_id, title, body) VALUES (?,?,?,?)',
                (content_type, row[0], row[1], row[2]),
            )
            total += 1

    db.commit()
    db.close()
    print(f'Search index rebuilt: {total} items indexed.')


def hash_password(args):
    """Generate a secure password hash for the admin account.

    Uses Werkzeug's generate_password_hash (pbkdf2:sha256 with 600k iterations).
    The output should be pasted into config.yaml under admin.password_hash.
    """
    password = getpass.getpass('Enter admin password: ')
    confirm = getpass.getpass('Confirm password: ')

    if password != confirm:
        print('ERROR: Passwords do not match.', file=sys.stderr)
        sys.exit(1)

    if len(password) < 8:
        print('ERROR: Password must be at least 8 characters.', file=sys.stderr)
        sys.exit(1)

    pw_hash = generate_password_hash(password)
    print('\nPaste this into your config.yaml under admin.password_hash:\n')
    print(f'  password_hash: "{pw_hash}"')


def generate_token(args):
    """Generate a review invitation token.

    Creates a cryptographically secure URL-safe token and inserts it into
    the review_tokens table. The resulting URL path can be shared with the
    intended reviewer.
    """
    import secrets

    from app import create_app

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    token_string = secrets.token_urlsafe(32)
    conn.execute(
        'INSERT INTO review_tokens (token, name, type) VALUES (?, ?, ?)',
        (token_string, args.name or '', args.type),
    )
    conn.commit()
    conn.close()

    print(f'Token generated for: {args.name or "anonymous"}')
    print(f'Type: {args.type}')
    print(f'URL path: /review/{token_string}')


def list_reviews(args):
    """List reviews filtered by status.

    Displays a summary of each review: ID, reviewer name, rating (if any),
    and a truncated message preview.
    """
    from app import create_app

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM reviews WHERE status = ? ORDER BY created_at DESC',
        (args.status,),
    ).fetchall()
    conn.close()

    if not rows:
        print(f'No {args.status} reviews.')
        return

    for row in rows:
        rating = f' [{row["rating"]}/5]' if row['rating'] else ''
        print(f'  [{row["id"]}] {row["reviewer_name"]}{rating} — {row["message"][:60]}...')


def purge_analytics(args):
    """Purge page view records older than a specified number of days.

    Helps manage database size over time. The retention period defaults to
    90 days but can be customized via the --days flag.
    """
    from app import create_app

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "DELETE FROM page_views WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        (f'-{args.days} days',),
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()

    print(f'Purged {count} page view records older than {args.days} days.')


# =============================================================
# QUERY AUDIT (Phase 12.1)
# =============================================================
#
# Each entry is `(label, sql, params, expected_scan)` — set expected_scan
# to True for queries where a full scan is the right plan (e.g. SELECT *
# with no WHERE on a tiny table). Those entries still print but don't fail
# the audit. Everything else flagged as a SCAN counts as a regression and
# the command exits non-zero.
#
# `manage.py query-audit` runs EXPLAIN QUERY PLAN against the real DB and
# prints each plan, marking it INDEX / SCAN / OK-SCAN.
#
# Adding a new hot path? Append a tuple here and re-run the audit.

_AUDIT_QUERIES = [
    (
        'inject_settings (every request)',
        'SELECT key, value FROM settings',
        (),
        # Settings is ~30 rows and we always want all of them. A full scan
        # is the optimal plan; the perf concern is mitigated by the TTL
        # cache in app/services/settings_svc.py.
        True,
    ),
    (
        'public blog index',
        "SELECT * FROM blog_posts WHERE status = 'published' ORDER BY published_at DESC LIMIT 10",
        (),
        False,
    ),
    (
        'blog post by slug',
        "SELECT * FROM blog_posts WHERE slug = ? AND status = 'published'",
        ('example-slug',),
        False,
    ),
    (
        'blog tags by slug → posts (JOIN)',
        'SELECT bp.* FROM blog_posts bp '
        'JOIN blog_post_tags bpt ON bp.id = bpt.post_id '
        'JOIN blog_tags bt ON bt.id = bpt.tag_id '
        "WHERE bp.status = 'published' AND bt.slug = ? "
        'ORDER BY bp.published_at DESC LIMIT 10',
        ('example-tag',),
        False,
    ),
    (
        'tags-for-posts batch loader',
        'SELECT bpt.post_id, bt.* FROM blog_tags bt '
        'JOIN blog_post_tags bpt ON bt.id = bpt.tag_id '
        'WHERE bpt.post_id IN (?, ?, ?) ORDER BY bt.name',
        (1, 2, 3),
        False,
    ),
    (
        'public testimonials by tier',
        "SELECT * FROM reviews WHERE status = 'approved' AND display_tier = ? "
        'ORDER BY created_at DESC',
        ('featured',),
        False,
    ),
    (
        'portfolio photos by tier',
        'SELECT * FROM photos WHERE display_tier = ? ORDER BY sort_order',
        ('featured',),
        False,
    ),
    (
        'contact rate-limit check',
        'SELECT COUNT(*) FROM contact_submissions WHERE ip_address = ? AND created_at > ?',
        ('127.0.0.1', '2026-01-01T00:00:00Z'),
        False,
    ),
    (
        'analytics IP lookup',
        'SELECT COUNT(*) FROM page_views WHERE ip_address = ?',
        ('127.0.0.1',),
        False,
    ),
    (
        'skills by domain (batch IN)',
        'SELECT * FROM skills WHERE domain_id IN (?, ?, ?) AND visible = 1 ORDER BY sort_order',
        (1, 2, 3),
        False,
    ),
]


def query_audit(args):  # noqa: ARG001 — argparse passes args even when unused
    """Run EXPLAIN QUERY PLAN on each documented hot query.

    Each query is tagged GOOD / SCAN based on whether SQLite picked an
    index or fell back to a full table scan. The exit code is 0 if every
    query is index-backed, 1 if any falls back to a scan — useful for
    catching regressions in CI later if we choose to wire this up.
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        print(f'ERROR: database not found at {db_path}', file=sys.stderr)
        print('Run `python manage.py init-db` first.', file=sys.stderr)
        sys.exit(2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print(f'Query audit against {db_path}\n')
    print('-' * 72)

    unexpected_scan = False
    for label, sql, params, expected_scan in _AUDIT_QUERIES:
        plan_rows = conn.execute(f'EXPLAIN QUERY PLAN {sql}', params).fetchall()
        plan_text = '\n  '.join(row['detail'] for row in plan_rows)
        # SQLite labels full table scans with "SCAN <table>" and indexed
        # access with "SEARCH ... USING [COVERING] INDEX".
        is_scan = any('SCAN' in row['detail'] for row in plan_rows)
        if not is_scan:
            marker = '✓ INDEX'
        elif expected_scan:
            marker = '~ OK-SCAN'
        else:
            marker = '✗ SCAN'
            unexpected_scan = True
        print(f'\n[{marker}] {label}')
        print(f'  SQL: {sql}')
        print(f'  PLAN:\n  {plan_text}')

    print('\n' + '-' * 72)
    if unexpected_scan:
        print('Unexpected full table scans found. Either add an index or')
        print('mark the entry expected_scan=True with a justification comment.')
    else:
        print('All audited queries use an expected plan. Schema is healthy.')

    conn.close()
    sys.exit(1 if unexpected_scan else 0)


# =============================================================
# COMPLEXITY REPORT (Phase 12.5)
# =============================================================
#
# Cyclomatic complexity ranking over the project's Python sources.
# Uses the stdlib `ast` module only — no radon/mccabe dependency —
# to keep this a zero-cost dev utility.
#
# Complexity is McCabe-style: start at 1, add 1 for each branching
# construct inside the function body. Nested functions and classes
# are reported separately so each entry's score reflects just its
# own body. Exit code is always 0 — this command is informational.
#
# Output columns: complexity, path:lineno, qualified name.

_COMPLEX_THRESHOLD = 10  # Traditional "complex" cutoff for the summary line


def _cyclomatic_complexity(func_node):
    """Return the McCabe cyclomatic complexity of a function AST node.

    Walks the function body without descending into nested function or
    class definitions — those are reported as their own entries by
    `_analyze_file` so their branches should not inflate the parent's
    score.

    Args:
        func_node: A FunctionDef, AsyncFunctionDef, or any AST node whose
            ``body`` and nested expressions should be counted.

    Returns:
        int: The complexity score (minimum 1).
    """
    complexity = 1
    stack = list(func_node.body)
    while stack:
        node = stack.pop()

        # Statements that introduce a branch
        if isinstance(
            node,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.Assert,
                ast.IfExp,
            ),
        ):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # `a and b and c` is two extra branches beyond the first operand
            complexity += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            # One branch for the `for`, plus one per `if` filter clause
            complexity += 1 + len(node.ifs)
        elif isinstance(node, ast.Match):
            complexity += len(node.cases)

        # Descend into children, but NOT into nested functions/classes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.iter_child_nodes(node):
            stack.append(child)

    return complexity


def _iter_python_files(roots):
    """Yield .py file paths under each root, pruning __pycache__ and dot-dirs.

    Roots may be directories or individual files.
    """
    for root in roots:
        if os.path.isfile(root):
            if root.endswith('.py'):
                yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in-place so os.walk skips these subtrees
            dirnames[:] = [d for d in dirnames if d != '__pycache__' and not d.startswith('.')]
            for fname in filenames:
                if fname.endswith('.py'):
                    yield os.path.join(dirpath, fname)


def _analyze_file(path, project_root):
    """Parse a .py file and return complexity entries for every function.

    Args:
        path: Absolute path to the .py file.
        project_root: Absolute path to the project root, used to build
            portable relative paths in the output.

    Returns:
        list[tuple[int, str, int, str]]: one entry per function/method as
        ``(complexity, relpath, lineno, qualname)``. Qualified names encode
        the enclosing class/function chain (e.g. ``MyClass.method`` or
        ``outer.<locals>.inner``). Lambdas are skipped. Files with syntax
        errors print a warning to stderr and contribute no entries.
    """
    try:
        with open(path, encoding='utf-8') as f:
            source = f.read()
    except OSError as e:
        print(f'warning: could not read {path}: {e}', file=sys.stderr)
        return []

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        print(f'warning: skipping {path} due to SyntaxError: {e}', file=sys.stderr)
        return []

    relpath = os.path.relpath(path, project_root)
    results = []

    def visit(node, name_stack):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                visit(child, name_stack + [node.name])
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = '.'.join(name_stack + [node.name]) if name_stack else node.name
            complexity = _cyclomatic_complexity(node)
            results.append((complexity, relpath, node.lineno, qualname))
            # Nested functions live in the function's own `<locals>` scope
            inner_stack = name_stack + [node.name, '<locals>']
            for child in node.body:
                visit(child, inner_stack)
            return

        # Descend into other containers to find functions hidden in if/try/etc.
        for child in ast.iter_child_nodes(node):
            visit(child, name_stack)

    for node in tree.body:
        visit(node, [])

    return results


def complexity_report(args):
    """Print the top N most complex functions in the codebase.

    Scans `app/` and `manage.py`, computes McCabe cyclomatic complexity
    per function, and prints a ranked table. Exits 0 — this command is
    informational, not a gate.

    Flags:
        --top N: Number of functions to show (default: 20). Must be >= 1.
    """
    top_n = args.top
    if top_n < 1:
        print('ERROR: --top must be >= 1', file=sys.stderr)
        sys.exit(2)

    project_root = os.path.dirname(os.path.abspath(__file__))
    roots = [
        os.path.join(project_root, 'app'),
        os.path.join(project_root, 'manage.py'),
    ]

    entries = []
    for path in _iter_python_files(roots):
        entries.extend(_analyze_file(path, project_root))

    if not entries:
        print('No Python files found.')
        return

    # Sort by (-complexity, relpath, lineno) for a deterministic ranking
    entries.sort(key=lambda e: (-e[0], e[1], e[2]))

    total = len(entries)
    shown = entries[:top_n]
    max_complexity = entries[0][0]
    mean_complexity = sum(e[0] for e in entries) / total
    over_threshold = sum(1 for e in entries if e[0] >= _COMPLEX_THRESHOLD)

    print(f'Cyclomatic complexity report (top {len(shown)} of {total} functions)')
    print('-' * 72)
    for complexity, relpath, lineno, qualname in shown:
        print(f'{complexity:>4}  {relpath}:{lineno}  {qualname}')
    print('-' * 72)
    print(
        f'Scanned {total} functions — max={max_complexity}, '
        f'mean={mean_complexity:.1f}, '
        f'{over_threshold} at or above threshold ({_COMPLEX_THRESHOLD}).'
    )


def translations(args):
    """Manage translation message catalogs.

    Subcommands:
        extract  — Scan source code and templates, generate messages.pot.
        init     — Create a new locale directory with an empty .po file.
        compile  — Compile all .po files to .mo (binary) for runtime use.
        update   — Update existing .po files with newly extracted messages.
    """
    import subprocess

    project_root = os.path.dirname(__file__)
    translations_dir = os.path.join(project_root, 'translations')
    pot_file = os.path.join(translations_dir, 'messages.pot')
    babel_cfg = os.path.join(project_root, 'babel.cfg')

    os.makedirs(translations_dir, exist_ok=True)

    action = args.action

    if action == 'extract':
        cmd = [
            sys.executable,
            '-m',
            'babel.messages.frontend',
            'extract',
            '-F',
            babel_cfg,
            '-o',
            pot_file,
            '--project',
            'resume-site',
            '--version',
            '0.2.0',
            '.',
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print(f'Messages extracted to {pot_file}')
        sys.exit(result.returncode)

    elif action == 'init':
        locale = args.locale
        if not locale:
            print(
                'ERROR: --locale is required for init. Example: python manage.py translations init --locale es',
                file=sys.stderr,
            )
            sys.exit(1)
        if not os.path.exists(pot_file):
            print(
                "ERROR: Run 'translations extract' first to generate messages.pot", file=sys.stderr
            )
            sys.exit(1)
        cmd = [
            sys.executable,
            '-m',
            'babel.messages.frontend',
            'init',
            '-i',
            pot_file,
            '-d',
            translations_dir,
            '-l',
            locale,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print(f"Locale '{locale}' initialized in {translations_dir}/{locale}/")
        sys.exit(result.returncode)

    elif action == 'compile':
        cmd = [
            sys.executable,
            '-m',
            'babel.messages.frontend',
            'compile',
            '-d',
            translations_dir,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print('Translation catalogs compiled.')
        sys.exit(result.returncode)

    elif action == 'update':
        if not os.path.exists(pot_file):
            print(
                "ERROR: Run 'translations extract' first to generate messages.pot", file=sys.stderr
            )
            sys.exit(1)
        cmd = [
            sys.executable,
            '-m',
            'babel.messages.frontend',
            'update',
            '-i',
            pot_file,
            '-d',
            translations_dir,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print('Translation catalogs updated with new messages.')
        sys.exit(result.returncode)

    else:
        print(f'Unknown translations action: {action}', file=sys.stderr)
        print('Available: extract, init, compile, update')
        sys.exit(1)


# =============================================================
# BACKUP / RESTORE (Phase 17.1)
# =============================================================
#
# Thin argparse glue on top of app/services/backups. The service module
# owns the real work (tar safety, SQLite online backup, atomic rename,
# pre-restore sidecar); this layer only resolves paths and translates
# service exceptions into exit codes.


def _resolve_backup_dir(explicit):
    """Resolve the backup output directory.

    Priority: --output-dir > RESUME_SITE_BACKUP_DIR env > <repo>/backups.
    """
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get('RESUME_SITE_BACKUP_DIR')
    if env:
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), 'backups'))


def _config_path_for_backup():
    """Return the config.yaml path, honouring RESUME_SITE_CONFIG."""
    return os.environ.get(
        'RESUME_SITE_CONFIG',
        os.path.join(os.path.dirname(__file__), 'config.yaml'),
    )


def _positive_int(raw):
    """argparse type for flags that must be an integer >= 1."""
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f'{raw!r} must be at least 1')
    return value


def backup(args):
    """Create, list, or prune backups.

    Flags (mutually exclusive for list/prune, otherwise default = create):
        --list              Print existing archives.
        --prune --keep N    Delete all but the N newest archives.
        --db-only           Database-only archive (fast snapshot).
        --output-dir DIR    Override the output location.
    """
    from app.services.backups import (
        BackupError,
        create_backup,
        list_backups,
        prune_backups,
    )

    output_dir = _resolve_backup_dir(args.output_dir)

    if args.list:
        entries = list_backups(output_dir)
        if not entries:
            print(f'No backups found in {output_dir}')
            return
        print(f'Backups in {output_dir}:')
        for entry in entries:
            size_mb = entry.size_bytes / (1024 * 1024)
            when = datetime.fromtimestamp(entry.mtime, tz=UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
            print(f'  {entry.name}  {size_mb:8.2f} MB  {when}')
        return

    if args.prune:
        if args.keep is None:
            print('ERROR: --prune requires --keep N', file=sys.stderr)
            sys.exit(2)
        try:
            deleted = prune_backups(output_dir, keep=args.keep)
        except ValueError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            sys.exit(2)
        print(f'Pruned {len(deleted)} archive(s); kept the {args.keep} newest.')
        return

    # Default: create a new backup
    db_path = _get_db_path()
    from app import create_app

    app = create_app()
    photos_dir = app.config.get('PHOTO_STORAGE')
    config_path = _config_path_for_backup()

    try:
        archive = create_backup(
            db_path=db_path,
            photos_dir=photos_dir,
            config_path=config_path if os.path.isfile(config_path) else None,
            output_dir=output_dir,
            db_only=args.db_only,
        )
    except BackupError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(3)
    except OSError as e:
        print(f'ERROR: I/O failure during backup: {e}', file=sys.stderr)
        sys.exit(3)

    print(f'Created backup: {archive}')


def restore(args):
    """Restore DB and photos from an archive.

    A pre-restore sidecar is always created in the backup directory
    before extraction, so an accidental restore of the wrong archive
    is recoverable. ``--force`` suppresses the interactive confirmation
    prompt but does NOT skip the sidecar.
    """
    from app.services.backups import (
        BackupError,
        BackupSecurityError,
        restore_backup,
    )

    archive_path = os.path.abspath(args.source)
    output_dir = _resolve_backup_dir(args.output_dir)

    if not os.path.isfile(archive_path):
        print(f'ERROR: backup file not found: {archive_path}', file=sys.stderr)
        sys.exit(2)

    if not args.force:
        if not sys.stdin.isatty():
            print(
                'ERROR: running in non-interactive mode; pass --force to confirm the restore.',
                file=sys.stderr,
            )
            sys.exit(5)
        print(f'About to restore from {archive_path}')
        print('This will overwrite the current database and photos.')
        print(f'A safety-net copy will be written to {output_dir}/pre-restore-*')
        answer = input("Type 'yes' to proceed: ").strip().lower()
        if answer != 'yes':
            print('Aborted.')
            sys.exit(0)

    db_path = _get_db_path()
    from app import create_app

    app = create_app()
    photos_dir = app.config.get('PHOTO_STORAGE')

    try:
        sidecar = restore_backup(
            archive_path=archive_path,
            db_path=db_path,
            photos_dir=photos_dir,
            output_dir=output_dir,
        )
    except FileNotFoundError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(2)
    except BackupSecurityError as e:
        print(f'ERROR: archive contains unsafe members: {e}', file=sys.stderr)
        sys.exit(4)
    except BackupError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(3)

    print(f'Restored from {archive_path}')
    print(f'Previous state saved to {sidecar}')


# ---------------------------------------------------------------------------
# API Token CLI commands (Phase 13.4)
# ---------------------------------------------------------------------------


def _print_api_token_banner(token):
    """Print the one-time reveal banner for a freshly-generated API token.

    The raw value is surfaced here and ONLY here — it is never persisted
    and never reappears on subsequent CLI invocations, so the banner is
    deliberately loud so a user piping to logs sees the warning.
    """
    banner = '=' * 64
    print(banner)
    print('API TOKEN — save this value now, it will not be shown again.')
    print(banner)
    print(f'ID:      {token.id}')
    print(f'Name:    {token.name}')
    print(f'Scope:   {token.scope}')
    print(f'Expires: {token.expires_at or "never"}')
    print(f'Token:   {token.raw}')
    print(banner)


def generate_api_token(args):
    """Create an API token with scoped access.

    The raw value is printed once to stdout; only its SHA-256 hash is
    stored. After this command exits, the raw token cannot be recovered
    from the database — rotate or generate a new one if lost.
    """
    from app import create_app
    from app.events import Events, emit
    from app.services.api_tokens import (
        InvalidScopeError,
        generate_token,
        parse_expires,
    )

    try:
        expires_at = parse_expires(args.expires)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(2)

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = generate_token(
            conn,
            name=args.name,
            scope=args.scope,
            expires_at=expires_at,
            created_by='admin',
        )
    except InvalidScopeError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(2)
    finally:
        conn.close()

    emit(
        Events.API_TOKEN_CREATED,
        name=result.name,
        scope=result.scope,
        created_by='admin',
        expires_at=result.expires_at or '',
        token_id=result.id,
    )
    _print_api_token_banner(result)


def rotate_api_token(args):
    """Rotate an existing named API token.

    Generates a fresh token inheriting scope + expiry from the newest
    active row matching ``--name``, then marks the old row revoked.
    The new raw value is printed once.
    """
    from app import create_app
    from app.events import Events, emit
    from app.services.api_tokens import TokenNotFoundError, rotate_token

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = rotate_token(conn, name=args.name, created_by='admin')
    except TokenNotFoundError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(2)
    finally:
        conn.close()

    emit(
        Events.API_TOKEN_CREATED,
        name=result.name,
        scope=result.scope,
        created_by='admin',
        expires_at=result.expires_at or '',
        token_id=result.id,
    )
    _print_api_token_banner(result)


def revoke_api_token(args):
    """Revoke an API token by id (soft delete — row retained for audit)."""
    from app import create_app
    from app.services.api_tokens import revoke_token

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        changed = revoke_token(conn, args.id)
    finally:
        conn.close()

    if not changed:
        print(
            f'ERROR: no active token with id {args.id} (already revoked or missing)',
            file=sys.stderr,
        )
        sys.exit(2)
    print(f'Revoked token id={args.id}')


def list_api_tokens(args):  # noqa: ARG001 — argparse passes args even when unused
    """List API tokens with their status and metadata."""
    from app import create_app
    from app.services.api_tokens import list_tokens

    app = create_app()
    db_path = app.config['DATABASE_PATH']

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        records = list_tokens(conn, include_revoked=True)
    finally:
        conn.close()

    if not records:
        print('No API tokens.')
        return

    header = f'{"ID":>4} {"NAME":<24} {"SCOPE":<16} {"STATUS":<9} {"EXPIRES":<20} LAST USED'
    print(header)
    print('-' * len(header))
    for r in records:
        status = 'revoked' if r.revoked else 'active'
        print(
            f'{r.id:>4} {r.name[:24]:<24} {r.scope[:16]:<16} '
            f'{status:<9} {(r.expires_at or "never")[:20]:<20} {r.last_used_at or "—"}'
        )


def main():
    """Parse command-line arguments and dispatch to the appropriate handler."""
    parser = argparse.ArgumentParser(description='resume-site management CLI')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Database initialization (delegates to migrate)
    subparsers.add_parser('init-db', help='Initialize the database (runs all migrations)')

    # Migration runner
    migrate_parser = subparsers.add_parser('migrate', help='Apply pending migrations')
    migrate_parser.add_argument('--status', action='store_true', help='Show migration status')
    migrate_parser.add_argument(
        '--dry-run', action='store_true', help='Print SQL without executing'
    )

    # Config validation
    subparsers.add_parser('config', help='Validate config.yaml')

    # Secret key generation / rotation
    subparsers.add_parser('generate-secret', help='Generate a secure secret_key')
    subparsers.add_parser('rotate-secret-key', help='Rotate secret_key in config.yaml (invalidates sessions)')
    subparsers.add_parser('rebuild-search-index', help='Rebuild FTS5 search index from all content')

    # Password hash generation
    subparsers.add_parser('hash-password', help='Generate an admin password hash')

    # Review token generation
    token_parser = subparsers.add_parser('generate-token', help='Generate a review invite token')
    token_parser.add_argument('--name', default='', help='Recipient name')
    token_parser.add_argument(
        '--type',
        default='recommendation',
        choices=['recommendation', 'client_review'],
        help='Token type',
    )

    # API token management (Phase 13.4)
    gat_parser = subparsers.add_parser(
        'generate-api-token', help='Generate an API token for programmatic access'
    )
    gat_parser.add_argument('--name', required=True, help='Human label for the token')
    gat_parser.add_argument(
        '--scope',
        required=True,
        help='Comma-separated subset of read,write,admin (e.g. "read,write")',
    )
    gat_parser.add_argument(
        '--expires',
        default=None,
        help='Expiry: Nd (days) / Nh (hours) / never / ISO date (YYYY-MM-DD). Default: never',
    )

    rat_parser = subparsers.add_parser(
        'rotate-api-token', help='Rotate a named API token (revokes the old one)'
    )
    rat_parser.add_argument('--name', required=True, help='Name of the token to rotate')

    rev_parser = subparsers.add_parser('revoke-api-token', help='Revoke an API token by id')
    rev_parser.add_argument('--id', type=int, required=True, help='Token id to revoke')

    subparsers.add_parser('list-api-tokens', help='List API tokens with status and metadata')

    # Review listing
    reviews_parser = subparsers.add_parser('list-reviews', help='List reviews by status')
    reviews_parser.add_argument(
        '--status',
        default='pending',
        choices=['pending', 'approved', 'rejected'],
        help='Filter by status',
    )

    # Analytics purge
    purge_parser = subparsers.add_parser('purge-analytics', help='Purge old analytics data')
    purge_parser.add_argument('--days', type=int, default=90, help='Days to retain')

    # Query audit (Phase 12.1) — runs EXPLAIN QUERY PLAN on hot queries
    subparsers.add_parser('query-audit', help='EXPLAIN QUERY PLAN on documented hot queries')

    # Complexity report (Phase 12.5) — ranks functions by cyclomatic complexity
    cr_parser = subparsers.add_parser(
        'complexity-report',
        help='Print the N most complex functions in the codebase',
    )
    cr_parser.add_argument(
        '--top', type=int, default=20, help='Number of functions to show (default: 20)'
    )

    # Backup (Phase 17.1) — create / list / prune site backups
    backup_parser = subparsers.add_parser(
        'backup', help='Create, list, or prune timestamped site backups'
    )
    backup_parser.add_argument('--output-dir', default=None, help='Where to write / read archives')
    backup_parser.add_argument('--db-only', action='store_true', help='Archive the database only')
    backup_mode = backup_parser.add_mutually_exclusive_group()
    backup_mode.add_argument('--list', action='store_true', help='List existing archives and exit')
    backup_mode.add_argument(
        '--prune', action='store_true', help='Delete old archives (requires --keep)'
    )
    backup_parser.add_argument(
        '--keep',
        type=_positive_int,
        default=None,
        help='When pruning, number of newest archives to retain (>= 1)',
    )

    # Restore (Phase 17.1) — restore DB and photos from an archive
    restore_parser = subparsers.add_parser(
        'restore', help='Restore the site database and photos from a backup'
    )
    restore_parser.add_argument(
        '--from',
        dest='source',
        required=True,
        help='Path to the .tar.gz archive to restore from',
    )
    restore_parser.add_argument(
        '--force',
        action='store_true',
        help='Skip the interactive confirmation prompt',
    )
    restore_parser.add_argument(
        '--output-dir',
        default=None,
        help='Where to write the pre-restore safety sidecar',
    )

    # Translation management
    trans_parser = subparsers.add_parser('translations', help='Manage translation files')
    trans_parser.add_argument(
        'action',
        choices=['extract', 'init', 'compile', 'update'],
        help='Translation action to perform',
    )
    trans_parser.add_argument('--locale', '-l', default=None, help='Locale code (for init)')

    args = parser.parse_args()

    commands = {
        'init-db': init_db,
        'migrate': migrate,
        'config': config_validate,
        'generate-secret': generate_secret,
        'rotate-secret-key': rotate_secret_key,
        'rebuild-search-index': rebuild_search_index,
        'hash-password': hash_password,
        'generate-token': generate_token,
        'generate-api-token': generate_api_token,
        'rotate-api-token': rotate_api_token,
        'revoke-api-token': revoke_api_token,
        'list-api-tokens': list_api_tokens,
        'list-reviews': list_reviews,
        'purge-analytics': purge_analytics,
        'query-audit': query_audit,
        'complexity-report': complexity_report,
        'backup': backup,
        'restore': restore,
        'translations': translations,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
