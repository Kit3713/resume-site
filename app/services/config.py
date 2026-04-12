"""
YAML Configuration Loader

Loads the infrastructure configuration from config.yaml, merges it with
sensible defaults, applies environment variable overrides, and validates
required fields. This module is called once at startup by the app factory —
the config is treated as immutable after that point (changes require a
process restart).

Configuration layers (highest precedence first):
1. Environment variables (RESUME_SITE_* prefix)
2. config.yaml
3. Built-in defaults

Configuration boundary:
- config.yaml / env vars (this module): Infrastructure settings that rarely
  change (secret key, SMTP credentials, admin password hash, allowed networks).
- Admin panel (SQLite settings table): Content and display settings that
  change frequently (site title, hero text, toggles, etc.).

Security:
- Uses yaml.safe_load() exclusively — never yaml.load() — to prevent
  arbitrary code execution from malicious YAML.
- Fails hard on missing secret_key (running without one is a security bug).
- Warns on weak or example secret_key values.
- Warns on missing password_hash (allows DB init before admin setup).
- Supports smtp.password_file for Docker/Podman secrets integration.
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
    'max_upload_size': 10 * 1024 * 1024,  # 10 MB default
    'session_timeout_minutes': 60,      # Admin session inactivity timeout
    'smtp': {
        'host': '',
        'port': 587,                    # STARTTLS default; use 465 for SMTP_SSL
        'user': '',
        'password': '',
        'password_file': '',            # Docker/Podman secrets: path to file containing password
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

# Environment variable mappings: env var name → config path (dot-separated).
# Precedence: env vars > config.yaml > defaults.
_ENV_VAR_MAP = {
    'RESUME_SITE_SECRET_KEY':         'secret_key',
    'RESUME_SITE_DATABASE_PATH':      'database_path',
    'RESUME_SITE_PHOTO_STORAGE':      'photo_storage',
    'RESUME_SITE_MAX_UPLOAD_SIZE':    'max_upload_size',
    'RESUME_SITE_SESSION_TIMEOUT':    'session_timeout_minutes',
    'RESUME_SITE_SMTP_HOST':          'smtp.host',
    'RESUME_SITE_SMTP_PORT':          'smtp.port',
    'RESUME_SITE_SMTP_USER':          'smtp.user',
    'RESUME_SITE_SMTP_PASSWORD':      'smtp.password',
    'RESUME_SITE_SMTP_PASSWORD_FILE': 'smtp.password_file',
    'RESUME_SITE_SMTP_RECIPIENT':     'smtp.recipient',
    'RESUME_SITE_ADMIN_USERNAME':     'admin.username',
    'RESUME_SITE_ADMIN_PASSWORD_HASH': 'admin.password_hash',
}

# Example/placeholder secret keys that should trigger a warning.
_WEAK_SECRET_KEYS = {
    'generate-a-random-key',
    'change-me',
    'secret',
    'your-secret-key',
    'test-secret-key-for-testing-only',
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


def _set_nested(config, dotted_key, value):
    """Set a value in a nested dict using a dot-separated key path.

    Example: _set_nested(config, 'smtp.host', 'mail.example.com')
    sets config['smtp']['host'] = 'mail.example.com'.
    """
    keys = dotted_key.split('.')
    target = config
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    # Coerce numeric strings for known integer fields
    if keys[-1] in ('port', 'max_upload_size', 'session_timeout_minutes'):
        try:
            value = int(value)
        except (ValueError, TypeError):
            pass
    target[keys[-1]] = value


def _apply_env_overrides(config):
    """Apply environment variable overrides to the config dict.

    Reads RESUME_SITE_* environment variables and overwrites the
    corresponding config values. This enables 12-factor app configuration
    and makes container deployments simpler (no config file needed for
    basic setups).
    """
    for env_var, config_path in _ENV_VAR_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            _set_nested(config, config_path, value)


def _resolve_password_file(config):
    """Read SMTP password from a file if smtp.password_file is set.

    Docker/Podman secrets are mounted as files. This allows the SMTP
    password to be provided as a secret file instead of a plaintext
    config value or environment variable.

    The file-based password only applies if smtp.password is empty
    (explicit password takes precedence over password_file).
    """
    password_file = config.get('smtp', {}).get('password_file', '')
    if password_file and not config['smtp'].get('password'):
        try:
            with open(password_file, 'r') as f:
                config['smtp']['password'] = f.read().strip()
        except (OSError, IOError) as e:
            print(
                f"WARNING: Could not read smtp.password_file '{password_file}': {e}",
                file=sys.stderr,
            )


def _validate_secret_key(secret_key):
    """Check the secret key for weakness and warn accordingly.

    Returns True if the key is acceptable, False if it's missing entirely.
    Warnings are printed but don't prevent startup — only a missing key
    is fatal.
    """
    if not secret_key:
        print("ERROR: 'secret_key' is required in config.yaml or RESUME_SITE_SECRET_KEY", file=sys.stderr)
        return False

    key_str = str(secret_key)

    if key_str.lower() in _WEAK_SECRET_KEYS:
        print(
            "WARNING: secret_key appears to be an example/placeholder value. "
            "Generate a secure one with: python manage.py generate-secret",
            file=sys.stderr,
        )

    if len(key_str) < 32:
        print(
            f"WARNING: secret_key is only {len(key_str)} characters. "
            "A minimum of 32 characters is recommended for production. "
            "Generate one with: python manage.py generate-secret",
            file=sys.stderr,
        )

    return True


def load_config(path):
    """Load and validate the configuration.

    Processing order:
    1. Read config.yaml and merge with defaults.
    2. Apply RESUME_SITE_* environment variable overrides.
    3. Resolve smtp.password_file if set.
    4. Validate required fields (secret_key).
    5. Warn on weak or missing credentials.

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

    # Apply environment variable overrides (highest precedence)
    _apply_env_overrides(config)

    # Resolve password from Docker/Podman secrets file
    _resolve_password_file(config)

    # Validate secret_key — fatal if missing
    if not _validate_secret_key(config.get('secret_key')):
        sys.exit(1)

    # password_hash is optional at startup (allows init-db before setting password)
    password_hash = config['admin'].get('password_hash', '')
    if not password_hash:
        print(
            "WARNING: admin.password_hash is empty. "
            "Set it with: python manage.py hash-password",
            file=sys.stderr,
        )
    elif not password_hash.startswith(('pbkdf2:sha256:', 'scrypt:', 'argon2')):
        print(
            "WARNING: admin.password_hash does not use a recognized strong algorithm. "
            "Expected pbkdf2:sha256, scrypt, or argon2. "
            "Regenerate with: python manage.py hash-password",
            file=sys.stderr,
        )

    return config
