# ==========================================================================
# resume-site Container Image
# ==========================================================================
#
# Builds an OCI-compliant container image for the resume-site Flask app.
# Designed for deployment with Podman or Docker behind a Caddy reverse proxy.
#
# Key decisions:
#   - python:3.12-slim: Small image (~150MB) with enough C libs for Pillow.
#   - Non-root user: Runs as appuser (UID 1000) for security.
#   - Layer caching: requirements.txt is copied before app code so dependency
#     installs are cached when only code changes.
#   - 2 Gunicorn workers: SQLite supports only one writer at a time; more
#     workers increase read throughput without write contention.
#   - Healthcheck uses Python stdlib (no curl on slim images).
#
# Build:
#   podman build -t resume-site .
#
# Run:
#   podman run -d --name resume-site \
#     -p 8080:8080 \
#     -v ./config.yaml:/app/config.yaml:ro,Z \
#     -v ./photos:/app/photos:Z \
#     -v ./data:/app/data:Z \
#     resume-site
#
# Volume mounts:
#   config.yaml  -> /app/config.yaml  (read-only, infrastructure config)
#   photos/      -> /app/photos       (uploaded portfolio images)
#   data/        -> /app/data         (SQLite database)
# ==========================================================================

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files to disk and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user for running the application (security best practice)
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install Python dependencies first for Docker layer caching.
# This layer is only rebuilt when requirements.txt changes.
# =============================================================
# resume-site Containerfile
# Multi-stage build, non-root, minimal attack surface
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

# OCI image labels
LABEL org.opencontainers.image.title="resume-site" \
      org.opencontainers.image.description="Self-hosted portfolio and blog engine" \
      org.opencontainers.image.source="https://github.com/Kit3713/resume-site" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.authors="Kit3713"

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
COPY app.py manage.py schema.sql ./

# Copy migrations if present (v0.2.0+)
COPY migration[s]/ ./migrations/

# Create writable directories and set ownership
RUN mkdir -p /app/data /app/photos /app/uploads && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Run with Gunicorn
ENTRYPOINT ["gunicorn", \
    "--bind", "0.0.0.0:8080", \
    "--workers", "2", \
    "--timeout", "120", \
    "--access-logfile", "-", \
    "--error-logfile", "-", \
    "app:create_app()"]
# =============================================================
# resume-site Containerfile
# Multi-stage build, non-root, minimal attack surface
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

# OCI image labels
LABEL org.opencontainers.image.title="resume-site" \
      org.opencontainers.image.description="Self-hosted portfolio and blog engine" \
      org.opencontainers.image.source="https://github.com/Kit3713/resume-site" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.authors="Kit3713"

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

# Copy the application source code
COPY app.py manage.py schema.sql config.example.yaml ./
COPY app/ app/

# Create directories for bind-mounted volumes and set ownership
RUN mkdir -p /app/data /app/photos && \
    chown -R appuser:appuser /app

# Switch to the non-root user for all subsequent operations
USER appuser

# Expose the Gunicorn port (Caddy reverse proxies to this)
EXPOSE 8080

# Health check using Python stdlib (curl is not available on slim images)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')" || exit 1

# Start Gunicorn with the Flask app factory.
# - 2 workers: optimal for SQLite (limited by single-writer constraint)
# - 120s timeout: accommodates slow photo uploads
# - Logs to stdout/stderr for container log collection
# Copy application code (respects .containerignore)
COPY app/ ./app/
COPY app.py manage.py schema.sql ./
COPY migrations/ ./migrations/

# Create writable directories and set ownership
RUN mkdir -p /app/data /app/photos /app/uploads && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Run with Gunicorn
ENTRYPOINT ["gunicorn", \
    "--bind", "0.0.0.0:8080", \
    "--workers", "2", \
    "--timeout", "120", \
    "--access-logfile", "-", \
    "--error-logfile", "-", \
    "app:create_app()"]
