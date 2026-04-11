import os
import sys

import yaml


_DEFAULT_CONFIG = {
    'secret_key': None,
    'database_path': 'data/site.db',
    'photo_storage': 'photos',
    'smtp': {
        'host': '',
        'port': 587,
        'user': '',
        'password': '',
        'recipient': '',
    },
    'admin': {
        'username': 'admin',
        'password_hash': '',
        'allowed_networks': [
            '127.0.0.0/8',
            '10.0.0.0/8',
            '192.168.0.0/16',
            '100.64.0.0/10',
        ],
    },
}


def _deep_merge(base, override):
    """Recursively merge override dict into base. Override wins on conflicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path):
    """Load YAML config, merge with defaults, validate required fields."""
    if not os.path.exists(path):
        print(f"ERROR: Config file not found: {path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and edit it.", file=sys.stderr)
        sys.exit(1)

    with open(path, 'r') as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(_DEFAULT_CONFIG, user_config)

    if not config.get('secret_key'):
        print("ERROR: 'secret_key' is required in config.yaml", file=sys.stderr)
        sys.exit(1)

    if not config['admin'].get('password_hash'):
        print(
            "WARNING: admin.password_hash is empty. "
            "Set it with: python manage.py hash-password",
            file=sys.stderr,
        )

    return config
