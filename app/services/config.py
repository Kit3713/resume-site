"""
YAML Configuration Loader

Loads the infrastructure configuration from config.yaml, merges it with
sensible defaults, and validates required fields. This module is called
once at startup by the app factory — the config is treated as immutable
after that point (changes require a process restart).

Configuration layers:
- config.yaml (this module): Infrastructure settings that rarely change
  (secret key, SMTP credentials, admin password hash, allowed networks).
- Admin panel (SQLite settings table): Content and display settings that
  change frequently (site title, hero text, toggles, etc.).

Security:
- Uses yaml.safe_load() exclusively — never yaml.load() — to prevent
  arbitrary code execution from malicious YAML.
- Fails hard on missing secret_key (running without one is a security bug).
- Warns on missing password_hash (allows DB init before admin setup).
"""

import os
import sys

import yaml


# Default configuration values. Users only need to override what they
# want to change in their config.yaml — everything else falls back here.
_DEFAULT_CONFIG = {
    'secret_key': None,                 # Required — no default for security
    'database_path': 'data/site.db',
    'photo_storage': 'photos',
    'smtp': {
        'host': '',
        'port': 587,                    # STARTTLS default; use 465 for SMTP_SSL
        'user': '',
        'password': '',
        'recipient': '',                # Admin's personal email for form submissions
    },
    'admin': {
        'username': 'admin',
        'password_hash': '',            # Generate with: python manage.py hash-password
        'allowed_networks': [
            '127.0.0.0/8',             # Localhost (local development)
            '10.0.0.0/8',              # Private network (Class A)
            '192.168.0.0/16',          # Private network (Class C)
            '100.64.0.0/10',           # Tailscale / CGNAT range
        ],
    },
}


def _deep_merge(base, override):
    """Recursively merge two dicts. Override values win on conflicts.

    Nested dicts are merged recursively rather than replaced, so users
    can specify partial overrides (e.g., only smtp.host without repeating
    all other SMTP fields).

    Args:
        base: The default configuration dict.
        override: The user-provided configuration dict.

    Returns:
        dict: The merged configuration.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path):
    """Load and validate the YAML configuration file.

    Reads the user's config.yaml, merges it with defaults, and validates
    that required fields are present. Exits the process on critical errors
    (missing file, missing secret_key) since the app cannot run safely
    without them.

    Args:
        path: Absolute or relative path to config.yaml.

    Returns:
        dict: The validated, merged configuration.
    """
    if not os.path.exists(path):
        print(f"ERROR: Config file not found: {path}", file=sys.stderr)
        print("Copy config.example.yaml to config.yaml and edit it.", file=sys.stderr)
        sys.exit(1)

    with open(path, 'r') as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(_DEFAULT_CONFIG, user_config)

    # secret_key is required — Flask sessions and CSRF protection depend on it
    if not config.get('secret_key'):
        print("ERROR: 'secret_key' is required in config.yaml", file=sys.stderr)
        sys.exit(1)

    # password_hash is optional at startup (allows init-db before setting password)
    if not config['admin'].get('password_hash'):
        print(
            "WARNING: admin.password_hash is empty. "
            "Set it with: python manage.py hash-password",
            file=sys.stderr,
        )

    return config
