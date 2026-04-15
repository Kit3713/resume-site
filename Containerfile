# =============================================================
# resume-site Containerfile
# Multi-stage build, non-root, minimal attack surface
#
# Build:
#   podman build -t resume-site .
#   podman build --build-arg IMAGE_VERSION=v0.3.0 -t resume-site:v0.3.0 .
#
# The IMAGE_VERSION build-arg (Phase 21.1) sources the OCI version
# label. CI sets it from the git tag in the publish workflow; local
# builds get ``dev`` if not overridden.
#
# Run:
#   podman run -d --name resume-site \
#     -p 8080:8080 \
#     -v ./config.yaml:/app/config.yaml:ro,Z \
#     -v resume-site-data:/app/data:Z \
#     -v resume-site-photos:/app/photos:Z \
#     -v resume-site-backups:/app/backups:Z \
#     resume-site
#
# Volume mounts:
#   config.yaml  -> /app/config.yaml  (read-only, infrastructure config)
#   data/        -> /app/data         (SQLite database)
#   photos/      -> /app/photos       (uploaded portfolio images)
#   backups/     -> /app/backups      (manage.py backup output; Phase 17.2)
#
# Health checks:
#   Liveness   ->  /healthz   (used by HEALTHCHECK; no I/O)
#   Readiness  ->  /readyz    (Phase 21.2; DB + migrations + disk + photos)
# =============================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies only in builder stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

# Build-time argument: CI injects the real version from the git tag
# (see .github/workflows/ci.yml `publish` job). Defaults to ``dev`` so
# local builds get a sensible label without manual --build-arg.
ARG IMAGE_VERSION=dev

# OCI image labels. The version label is the only field that changes
# per release, so it sources from the IMAGE_VERSION ARG above.
LABEL org.opencontainers.image.title="resume-site" \
      org.opencontainers.image.description="Self-hosted portfolio and blog engine" \
      org.opencontainers.image.source="https://github.com/Kit3713/resume-site" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.authors="Kit3713"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install only runtime system deps (curl for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    # Create non-root user
    groupadd -r appuser -g 1000 && \
    useradd -r -u 1000 -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (respects .containerignore)
COPY app/ ./app/
COPY app.py manage.py schema.sql babel.cfg ./

# Copy migrations, seeds, and translations (v0.2.0+)
COPY migration[s]/ ./migrations/
COPY seed[s]/ ./seeds/
COPY translation[s]/ ./translations/

# Create writable directories and set ownership
RUN mkdir -p /app/data /app/photos /app/uploads && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check (liveness only). The Phase 21.2 readiness endpoint
# (/readyz) does deeper I/O — DB connectivity, migration freshness,
# disk headroom — and is intentionally NOT used by HEALTHCHECK because
# Podman/Docker's only response to a failed healthcheck is to mark the
# container unhealthy (and on some configs, kill it). Leave the
# orchestrator-grade readiness probe to k8s/Nomad — see compose.yaml
# for the commented readiness probe block.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

# Run with Gunicorn
# - 2 workers: optimal for SQLite (limited by single-writer constraint)
# - 120s timeout: accommodates slow photo uploads
# - Logs to stdout/stderr for container log collection
ENTRYPOINT ["gunicorn", \
    "--bind", "0.0.0.0:8080", \
    "--workers", "2", \
    "--timeout", "120", \
    "--access-logfile", "-", \
    "--error-logfile", "-", \
    "app:create_app()"]
