# Logging

resume-site uses Python's stdlib `logging` module with structured output
configured via environment variables.  No third-party logging packages are
required.

---

## Environment Variables

| Variable | Values | Default | Notes |
|---|---|---|---|
| `RESUME_SITE_LOG_FORMAT` | `json`, `human` | `json` | JSON is one object per line (newline-delimited JSON); `human` is a compact text line for local dev |
| `RESUME_SITE_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `INFO` | Standard Python log level names |

Both are read at app startup and affect the root logger.

---

## Log Schema (JSON mode)

Every request produces one JSON record on stderr:

```json
{
  "timestamp": "2026-04-15T13:54:30Z",
  "level": "INFO",
  "logger": "app.request",
  "message": "GET /portfolio 200 42ms",
  "module": "__init__",
  "request_id": "a1b2c3d4-...",
  "client_ip_hash": "0f0f1a2b3c4d5e6f",
  "method": "GET",
  "path": "/portfolio",
  "status_code": 200,
  "duration_ms": 42.1,
  "user_agent": "Mozilla/5.0 ..."
}
```

**PII posture:** No full IPs, query strings, POST bodies, passwords, or tokens
are logged.  `client_ip_hash` is a salted SHA-256 truncation (per-deployment
salt from `secret_key`) so log files alone cannot correlate visitors across
deployments.

---

## Log Rotation

### Container Deployments (recommended)

In containerised setups the app writes to **stderr only**.  The container
runtime's log driver handles rotation automatically.

#### Podman (journald driver — default)

Podman sends container stderr to the systemd journal by default.  Journal
rotation is controlled by journald:

```ini
# /etc/systemd/journald.conf (or a drop-in)
[Journal]
SystemMaxUse=500M        # total disk cap for all journal entries
MaxRetentionSec=30day    # drop entries older than 30 days
```

Reload with `sudo systemctl restart systemd-journald`.  Query logs:

```bash
journalctl --user -u resume-site.service --since "1 hour ago"
journalctl --user -u resume-site.service -o json   # raw JSON records
```

#### Docker (json-file driver — default)

Docker's `json-file` driver stores logs on disk.  Configure rotation in
`/etc/docker/daemon.json` or per-container:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
```

Or per-container in `compose.yaml`:

```yaml
services:
  resume-site:
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

#### Forwarding to a Log Aggregator

For centralised logging (Loki, ELK, CloudWatch), configure the container
runtime's log driver to forward directly:

| Aggregator | Driver | Example |
|---|---|---|
| **Grafana Loki** | `docker plugin install grafana/loki-docker-driver:latest` then `--log-driver=loki` | `--log-opt loki-url=http://loki:3100/loki/api/v1/push` |
| **AWS CloudWatch** | `awslogs` | `--log-driver=awslogs --log-opt awslogs-group=resume-site` |
| **Fluentd / ELK** | `fluentd` | `--log-driver=fluentd --log-opt fluentd-address=localhost:24224` |

Because the app emits newline-delimited JSON, aggregators can parse fields
(request_id, status_code, duration_ms) directly without a custom parser.

### Bare-Metal Deployments (non-container)

When running Gunicorn directly (no container), use Gunicorn's built-in log
file options together with Python's `RotatingFileHandler` for the
application log.

#### Gunicorn Access + Error Logs

```bash
gunicorn app:create_app() \
  --bind 0.0.0.0:8080 \
  --access-logfile /var/log/resume-site/access.log \
  --error-logfile  /var/log/resume-site/error.log
```

Pair with `logrotate`:

```
# /etc/logrotate.d/resume-site
/var/log/resume-site/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate          # Gunicorn keeps the FD open; copytruncate avoids SIGHUP
}
```

#### Application Log (RotatingFileHandler)

To rotate the application's structured log to a file instead of stderr,
add a `RotatingFileHandler` in the Gunicorn config:

```python
# gunicorn.conf.py
import logging
from logging.handlers import RotatingFileHandler

def post_fork(server, worker):
    handler = RotatingFileHandler(
        '/var/log/resume-site/app.log',
        maxBytes=50 * 1024 * 1024,   # 50 MB per file
        backupCount=5,                # keep 5 rotated files
    )
    handler.setLevel(logging.DEBUG)
    # The app's configure_logging() already set a JSON formatter on the
    # root logger.  Re-use it so rotation doesn't change the schema.
    root = logging.getLogger()
    if root.handlers:
        handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
```

Set `RESUME_SITE_LOG_FORMAT=json` and `RESUME_SITE_LOG_LEVEL=INFO` in
the environment (or in a systemd unit `Environment=` line) before starting
Gunicorn.

---

## Request Correlation

Every request is assigned a UUID4 `request_id` (or propagated from an
inbound `X-Request-ID` header for reverse-proxy correlation).  The ID
appears in:

- Every structured log record (`request_id` field)
- The `X-Request-ID` response header
- Error responses (JSON `request_id` field or text body)

To trace a user-reported error:

```bash
# Container (Podman/journald)
journalctl --user -u resume-site.service -o json | jq 'select(.request_id == "abcd1234-...")'

# Container (Docker json-file)
docker logs resume-site 2>&1 | jq 'select(.request_id == "abcd1234-...")'

# Bare-metal
jq 'select(.request_id == "abcd1234-...")' /var/log/resume-site/app.log
```
