#!/bin/sh
# =============================================================
# resume-site container entrypoint
#
# Runs once per container start, before Gunicorn takes over. Its only
# job is to make the "first run on a fresh volume" experience smooth:
# run pending migrations, seed defaults, then exec Gunicorn.
#
# Every operation is idempotent:
#   * ``manage.py init-db`` calls migrate + seeds. Migrations consult
#     ``schema_version`` so already-applied entries are skipped;
#     seeds use ``INSERT OR IGNORE`` so default rows are never
#     duplicated.
#   * The corruption check in ``manage.py migrate``
#     (``_check_db_not_corrupt``) aborts the start if the DB file is
#     truncated or has failed ``PRAGMA integrity_check`` — preferable
#     to silently applying a fresh schema on top of damaged data.
#
# Deployment upgrades just need ``podman pull`` + restart; the
# entrypoint picks up new migrations automatically.
# =============================================================
set -eu

# Restrict the permissions of any file the container creates below.
# SQLite's default is 0644 which triggers the startup audit
# "Database file is world-readable" warning. 027 here gives
# 0640 / 0750 — readable by appuser + group, opaque to everyone else.
# The container only has appuser + root, so the practical effect is
# just silencing the warning, but this matches production-server
# expectations where the DB file sits on a shared filesystem.
umask 027

echo '--- resume-site entrypoint: running migrations + seeds ---'
python manage.py init-db

# Tighten any pre-existing files (e.g., restored from a backup with
# permissive modes) before gunicorn takes over.
if [ -d /app/data ]; then
    chmod 750 /app/data || true
    find /app/data -maxdepth 1 -type f -exec chmod 640 {} + 2>/dev/null || true
fi

echo '--- resume-site entrypoint: handing off to Gunicorn ---'
exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    "app:create_app()"
