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
