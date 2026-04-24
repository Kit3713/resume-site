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
# Phase 26.2 (#28, #53):
#
# --preload — import the app once in the master and fork workers from
# that pre-loaded state. Measured 500-800 ms cold-start win plus lower
# steady-state RSS via copy-on-write. The page_views drainer (Phase
# 25.2) and webhook thread pool (Phase 25.3) are started lazily on
# first use, *after* fork, so --preload is safe. The Flask-Limiter
# memory storage is per-worker; no cross-worker state to confuse.
#
# --max-requests 2000 + --max-requests-jitter 200 — workers recycle
# every ~2 000 requests (random 0-200 offset per worker so they
# don't all recycle simultaneously). Guards against Pillow / Jinja /
# SQLite statement-cache memory creep that was previously only
# released at container restart. Pairs naturally with --preload:
# the recycled worker re-forks from the pre-loaded master, so
# recycling is cheap.
exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --timeout 120 \
    --preload \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile - \
    --error-logfile - \
    "app:create_app()"
