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
    list-reviews         Display reviews filtered by status (pending/approved/rejected).
    purge-analytics      Delete page view records older than N days.
    translations         Manage translation files (extract, init, compile, update).

Usage:
    python manage.py init-db
    python manage.py migrate
    python manage.py migrate --status
    python manage.py migrate --dry-run
    python manage.py hash-password
    python manage.py generate-secret
    python manage.py generate-token --name "John Doe" --type recommendation
    python manage.py list-reviews --status pending
    python manage.py purge-analytics --days 90
    python manage.py translations extract
    python manage.py translations init es
    python manage.py translations compile
    python manage.py translations update
"""

import argparse
import getpass
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash


def _get_db_path():
    """Return the configured database path by loading the app config."""
    from app import create_app
    app = create_app()
    return app.config['DATABASE_PATH']


def _get_migrations_dir():
    """Return the path to the migrations/ directory."""
    return os.path.join(os.path.dirname(__file__), 'migrations')


def _ensure_schema_version_table(conn):
    """Create the schema_version tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    conn.commit()


def _get_applied_versions(conn):
    """Return a set of migration version numbers that have been applied."""
    return {row[0] for row in conn.execute('SELECT version FROM schema_version').fetchall()}


def _detect_existing_db(conn):
    """Return True if this looks like a v0.1.0 database (settings table exists)."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    return 'settings' in tables


def _list_migration_files(migrations_dir):
    """Return sorted (version, filename) pairs from the migrations directory."""
    if not os.path.isdir(migrations_dir):
        return []
    files = []
    for fname in sorted(os.listdir(migrations_dir)):
        if fname.endswith('.sql') and fname[0].isdigit():
            try:
                version = int(fname.split('_')[0])
                files.append((version, fname))
            except ValueError:
                pass
    return files


def init_db(args):
    """Initialize the database by running all pending migrations.

    Delegates to the migrate command internally. Running init-db on an
    already-initialized database is safe — it only applies pending migrations.
    """
    # Reuse the migrate logic with no flags
    class _Args:
        status = False
        dry_run = False
    migrate(_Args())


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
        conn.execute(
            "INSERT INTO schema_version (version, name) VALUES (1, '001_baseline.sql')"
        )
        conn.commit()
        applied.add(1)
        print("Detected existing database — marked 001_baseline as applied.")

    migration_files = _list_migration_files(migrations_dir)
    if not migration_files:
        print("No migration files found in migrations/")
        conn.close()
        return

    if args.status:
        print("Migration status:")
        for version, fname in migration_files:
            status = 'applied ' if version in applied else 'pending '
            print(f"  [{status}] {fname}")
        conn.close()
        return

    pending = [(v, f) for v, f in migration_files if v not in applied]
    if not pending:
        print("All migrations are already applied.")
        conn.close()
        return

    for version, fname in pending:
        path = os.path.join(migrations_dir, fname)
        with open(path, 'r') as f:
            sql = f.read()

        if args.dry_run:
            print(f"-- DRY RUN: {fname}")
            print(sql)
            print()
            continue

        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                (version, fname),
            )
            conn.commit()
            print(f"Applied: {fname}")
        except Exception as e:
            print(f"ERROR applying {fname}: {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)

    conn.close()
    if not args.dry_run:
        print("Migrations complete.")


def config_validate(args):
    """Validate config.yaml against expected structure and values.

    Checks for:
    - Required fields (secret_key)
    - Unknown/unexpected keys (typos, settings-layer values in config)
    - Type correctness for known fields
    - Secret key strength
    - CIDR network format validity
    """
    import ipaddress

    from app.services.config import load_config, _WEAK_SECRET_KEYS

    _VALID_TOP_KEYS = {
        'secret_key', 'database_path', 'photo_storage', 'max_upload_size',
        'session_timeout_minutes', 'smtp', 'admin',
    }
    _VALID_SMTP_KEYS = {'host', 'port', 'user', 'password', 'password_file', 'recipient'}
    _VALID_ADMIN_KEYS = {'username', 'password_hash', 'allowed_networks'}

    # Settings-layer keys that don't belong in config.yaml
    _SETTINGS_KEYS = {
        'site_title', 'site_tagline', 'dark_mode_default', 'availability_status',
        'contact_form_enabled', 'contact_email_visible', 'contact_phone_visible',
        'contact_github_url', 'contact_linkedin_url', 'resume_visibility',
        'case_studies_enabled', 'testimonial_display_mode', 'analytics_retention_days',
        'hero_heading', 'hero_subheading', 'hero_tagline', 'accent_color',
        'logo_mode', 'footer_text',
    }

    config_path = os.environ.get(
        'RESUME_SITE_CONFIG',
        os.path.join(os.path.dirname(__file__), 'config.yaml'),
    )

    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    import yaml as _yaml
    with open(config_path, 'r') as f:
        raw = _yaml.safe_load(f) or {}

    errors = []
    warnings = []

    # Check for unknown top-level keys
    for key in raw:
        if key in _SETTINGS_KEYS:
            warnings.append(
                f"'{key}' belongs in the admin settings panel, not config.yaml. "
                "It will be ignored here."
            )
        elif key not in _VALID_TOP_KEYS:
            warnings.append(f"Unknown key '{key}' in config.yaml (possible typo?).")

    # Check required fields
    if 'secret_key' not in raw:
        errors.append("Required field 'secret_key' is missing.")
    else:
        sk = str(raw['secret_key'])
        if sk.lower() in _WEAK_SECRET_KEYS:
            warnings.append("secret_key is an example/placeholder value.")
        if len(sk) < 32:
            warnings.append(f"secret_key is only {len(sk)} chars (32+ recommended).")

    # Validate SMTP section
    smtp = raw.get('smtp', {})
    if isinstance(smtp, dict):
        for key in smtp:
            if key not in _VALID_SMTP_KEYS:
                warnings.append(f"Unknown key 'smtp.{key}' (possible typo?).")
        if 'port' in smtp and not isinstance(smtp['port'], int):
            errors.append("smtp.port must be an integer.")
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
        print("ERRORS:")
        for e in errors:
            print(f"  ✗ {e}")
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  ⚠ {w}")
    if not errors and not warnings:
        print("✓ config.yaml is valid.")
    elif errors:
        sys.exit(1)


def generate_secret(args):
    """Generate a cryptographically secure secret_key for config.yaml.

    Uses Python's secrets module to produce a 64-byte (512-bit) URL-safe
    random string. The output can be pasted directly into config.yaml.
    """
    import secrets as _secrets
    key = _secrets.token_urlsafe(64)
    print(f"\nPaste this into your config.yaml as secret_key:\n")
    print(f'  secret_key: "{key}"')


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
    print(f'\nPaste this into your config.yaml under admin.password_hash:\n')
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

    print(f"Token generated for: {args.name or 'anonymous'}")
    print(f"Type: {args.type}")
    print(f"URL path: /review/{token_string}")


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
        print(f"No {args.status} reviews.")
        return

    for row in rows:
        rating = f" [{row['rating']}/5]" if row['rating'] else ""
        print(f"  [{row['id']}] {row['reviewer_name']}{rating} — {row['message'][:60]}...")


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

    print(f"Purged {count} page view records older than {args.days} days.")


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
            sys.executable, '-m', 'babel.messages.frontend', 'extract',
            '-F', babel_cfg,
            '-o', pot_file,
            '--project', 'resume-site',
            '--version', '0.2.0',
            '.',
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print(f"Messages extracted to {pot_file}")
        sys.exit(result.returncode)

    elif action == 'init':
        locale = args.locale
        if not locale:
            print("ERROR: --locale is required for init. Example: python manage.py translations init --locale es",
                  file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(pot_file):
            print("ERROR: Run 'translations extract' first to generate messages.pot",
                  file=sys.stderr)
            sys.exit(1)
        cmd = [
            sys.executable, '-m', 'babel.messages.frontend', 'init',
            '-i', pot_file,
            '-d', translations_dir,
            '-l', locale,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print(f"Locale '{locale}' initialized in {translations_dir}/{locale}/")
        sys.exit(result.returncode)

    elif action == 'compile':
        cmd = [
            sys.executable, '-m', 'babel.messages.frontend', 'compile',
            '-d', translations_dir,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print("Translation catalogs compiled.")
        sys.exit(result.returncode)

    elif action == 'update':
        if not os.path.exists(pot_file):
            print("ERROR: Run 'translations extract' first to generate messages.pot",
                  file=sys.stderr)
            sys.exit(1)
        cmd = [
            sys.executable, '-m', 'babel.messages.frontend', 'update',
            '-i', pot_file,
            '-d', translations_dir,
        ]
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode == 0:
            print("Translation catalogs updated with new messages.")
        sys.exit(result.returncode)

    else:
        print(f"Unknown translations action: {action}", file=sys.stderr)
        print("Available: extract, init, compile, update")
        sys.exit(1)


def main():
    """Parse command-line arguments and dispatch to the appropriate handler."""
    parser = argparse.ArgumentParser(description='resume-site management CLI')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Database initialization (delegates to migrate)
    subparsers.add_parser('init-db', help='Initialize the database (runs all migrations)')

    # Migration runner
    migrate_parser = subparsers.add_parser('migrate', help='Apply pending migrations')
    migrate_parser.add_argument('--status', action='store_true', help='Show migration status')
    migrate_parser.add_argument('--dry-run', action='store_true', help='Print SQL without executing')

    # Config validation
    subparsers.add_parser('config', help='Validate config.yaml')

    # Secret key generation
    subparsers.add_parser('generate-secret', help='Generate a secure secret_key')

    # Password hash generation
    subparsers.add_parser('hash-password', help='Generate an admin password hash')

    # Review token generation
    token_parser = subparsers.add_parser('generate-token', help='Generate a review invite token')
    token_parser.add_argument('--name', default='', help='Recipient name')
    token_parser.add_argument('--type', default='recommendation',
                              choices=['recommendation', 'client_review'],
                              help='Token type')

    # Review listing
    reviews_parser = subparsers.add_parser('list-reviews', help='List reviews by status')
    reviews_parser.add_argument('--status', default='pending',
                                choices=['pending', 'approved', 'rejected'],
                                help='Filter by status')

    # Analytics purge
    purge_parser = subparsers.add_parser('purge-analytics', help='Purge old analytics data')
    purge_parser.add_argument('--days', type=int, default=90, help='Days to retain')

    # Translation management
    trans_parser = subparsers.add_parser('translations', help='Manage translation files')
    trans_parser.add_argument('action', choices=['extract', 'init', 'compile', 'update'],
                              help='Translation action to perform')
    trans_parser.add_argument('--locale', '-l', default=None, help='Locale code (for init)')

    args = parser.parse_args()

    commands = {
        'init-db': init_db,
        'migrate': migrate,
        'config': config_validate,
        'generate-secret': generate_secret,
        'hash-password': hash_password,
        'generate-token': generate_token,
        'list-reviews': list_reviews,
        'purge-analytics': purge_analytics,
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
