FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install dependencies (layer caching: only re-run if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py manage.py schema.sql config.example.yaml ./
COPY app/ app/

# Create volume mount points
RUN mkdir -p /app/data /app/photos && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')" || exit 1

# Gunicorn: 2 workers (SQLite doesn't benefit from many concurrent writers)
ENTRYPOINT ["gunicorn", \
    "--bind", "0.0.0.0:8080", \
    "--workers", "2", \
    "--timeout", "120", \
    "--access-logfile", "-", \
    "--error-logfile", "-", \
    "app:create_app()"]
