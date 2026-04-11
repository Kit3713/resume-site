#!/usr/bin/env python3
"""CLI tools for resume-site management."""

import argparse
import getpass
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash


def init_db(args):
    """Initialize the database with the full schema."""
    from app import create_app

    app = create_app()

    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if not os.path.exists(schema_path):
        print(f"ERROR: schema.sql not found at {schema_path}", file=sys.stderr)
        sys.exit(1)

    with open(schema_path, 'r') as f:
        schema = f.read()

    db_path = app.config['DATABASE_PATH']
    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.close()

    print(f"Database initialized at: {db_path}")


def hash_password(args):
    """Generate a password hash for config.yaml."""
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


def main():
    parser = argparse.ArgumentParser(description='resume-site management CLI')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    subparsers.add_parser('init-db', help='Initialize the database')
    subparsers.add_parser('hash-password', help='Generate an admin password hash')

    args = parser.parse_args()

    if args.command == 'init-db':
        init_db(args)
    elif args.command == 'hash-password':
        hash_password(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
