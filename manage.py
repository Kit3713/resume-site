#!/usr/bin/env python3
"""
resume-site Management CLI

Provides command-line tools for server administration tasks that don't
require the admin panel (e.g., initial setup, headless management).

Commands:
    init-db          Initialize the SQLite database with the full schema.
                     Safe to run multiple times (all CREATE TABLE use IF NOT EXISTS).
    hash-password    Generate a pbkdf2:sha256 password hash for config.yaml.
    generate-token   Create a review invitation token for a trusted contact.
    list-reviews     Display reviews filtered by status (pending/approved/rejected).
    purge-analytics  Delete page view records older than N days.

Usage:
    python manage.py init-db
    python manage.py hash-password
    python manage.py generate-token --name "John Doe" --type recommendation
    python manage.py list-reviews --status pending
    python manage.py purge-analytics --days 90
"""

import argparse
import getpass
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash


def init_db(args):
    """Initialize the database with the full schema.

    Reads schema.sql and executes it against the configured database path.
    All tables use CREATE TABLE IF NOT EXISTS, making this command safe to
    run multiple times without data loss. Default settings are seeded via
    INSERT OR IGNORE.
    """
    from app import create_app

    app = create_app()

    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if not os.path.exists(schema_path):
        print(f"ERROR: schema.sql not found at {schema_path}", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, 'r') as f:
        schema = f.read()

    # Ensure the database directory exists
    db_path = app.config['DATABASE_PATH']
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.close()

    print(f"Database initialized at: {db_path}")


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


def main():
    """Parse command-line arguments and dispatch to the appropriate handler."""
    parser = argparse.ArgumentParser(description='resume-site management CLI')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Database initialization
    subparsers.add_parser('init-db', help='Initialize the database')

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

    args = parser.parse_args()

    if args.command == 'init-db':
        init_db(args)
    elif args.command == 'hash-password':
        hash_password(args)
    elif args.command == 'generate-token':
        generate_token(args)
    elif args.command == 'list-reviews':
        list_reviews(args)
    elif args.command == 'purge-analytics':
        purge_analytics(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
