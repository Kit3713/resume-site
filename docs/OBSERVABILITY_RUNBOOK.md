# Observability Runbook

**Phase:** 18.14 of [`ROADMAP_v0.3.0.md`](../ROADMAP_v0.3.0.md)
**Companion docs:** [`LOGGING.md`](./LOGGING.md), [`alerting-rules.md`](./alerting-rules.md), [`PRODUCTION.md`](./PRODUCTION.md), [`PERFORMANCE.md`](../PERFORMANCE.md)

All the observability tooling in the world is useless without a process for
using it. This runbook is that process — the when and how of every tool this
repo ships, from "I'm about to write a new feature" to "I just deployed and
want to know if it broke something."

If you're an operator looking at a firing alert, go to
[`alerting-rules.md`](./alerting-rules.md) first — that's the incident
runbook. This file is the _development_ runbook.

---

## Quick index

| Tool | Fire when… | Entry point |
|---|---|---|
| Structured logs | Something's happening right now and you need a transcript | stderr → journald / `docker logs` |
| `/metrics` (Prometheus) | Something happens a lot and you want rates / percentiles | `GET /metrics` (feature-flagged) |
| Alerting rules | Something crossed a threshold — you want to be paged | `docs/alerting-rules.yaml` |
| Grafana dashboard | You need to see the whole picture at a glance | `docs/grafana-dashboard.json` |
| Synthetic (level 1) | Verify the site is reachable from the outside | Uptime Kuma / UptimeRobot against `/healthz` |
| Synthetic (level 2) | Verify five key pages render + respond in < 2 s | `tests/synthetic/healthcheck.sh` |
| Synthetic (level 3) | Verify a full user journey works end-to-end | `tests/synthetic/monitor.py` (Playwright) |
| `manage.py query-audit` | Looking for DB hotspots / N+1 regressions | `python manage.py query-audit` |
| `manage.py profile` | Profiling routes before/after a change | `python manage.py profile` |
| `manage.py mutation-report` | Checking whether tests would catch a bug | `python manage.py mutation-report` |
| `scripts/benchmark_routes.py` | Hot-path benchmark for PERFORMANCE.md | `python scripts/benchmark_routes.py 100` |
| Load tests (locust) | What happens under realistic concurrent load | `locust -f tests/loadtests/locustfile.py` |

---

## When to reach for each tool

### 1. Something is broken right now

Start with **structured logs** (level=ERROR or level=WARNING) and
`X-Request-ID` correlation. Every request emits a JSON record on
stderr (schema in [`LOGGING.md`](./LOGGING.md)). Filter by `request_id`
to trace one user's path through the app:

```bash
# Podman / Quadlet — stream from journald
journalctl --user -u resume-site.service -f \
  -o json --output-fields=MESSAGE | jq 'select(.request_id)'

# Docker compose
docker logs -f resume-site 2>&1 | jq 'select(.level=="ERROR")'

# Pull a specific request trail
docker logs resume-site 2>&1 | jq -r \
  'select(.request_id=="a1b2c3d4-...") | "\(.timestamp) \(.level) \(.message)"'
```

If you already have the `request_id` from a user-reported error (every
error response carries `X-Request-ID`), this is a one-grep operation.

### 2. Something is happening a lot

Reach for **`/metrics`**. All counters, gauges, and histograms live
there in Prometheus exposition format.

```bash
# Hit /metrics directly (requires metrics_enabled=true in admin
# panel and your IP inside metrics_allowed_networks)
curl -s https://your-domain/metrics | grep resume_site_
```

The `resume_site_errors_total{category,status}` counter splits 4xx/5xx by
category — see [`alerting-rules.md`](./alerting-rules.md) for the
taxonomy. `resume_site_login_attempts_total{outcome}` is the brute-force
canary. Full metric list in `app/services/metrics.py`.

Instead of grepping by hand, import
[`grafana-dashboard.json`](./grafana-dashboard.json) into Grafana — the
11 panels there are the default view this project expects operators to
keep open. Setup walk-through below (§ "Setting up Prometheus + Grafana").

### 3. You want to be told when it breaks

Ship [`alerting-rules.yaml`](./alerting-rules.yaml) to Prometheus and
wire Alertmanager to a pager / chat / email channel. Every rule has a
`runbook_url` annotation pointing at the exact section in
[`alerting-rules.md`](./alerting-rules.md) — the on-call engineer
clicks the link and lands in the right place.

The set of shipped alerts:

- `ResumeInternalErrorRate` (critical) — any unhandled exception
- `ResumeAuthErrorSpike` (warning) — 401/403 flood
- `ResumeBruteForce` (warning) — sustained invalid-credential attempts
- `ResumeHighLatency` (warning) — p95 > 1 s
- `ResumeHighRequestRate` (info) — > 100 req/min
- `ResumeNoTraffic` (warning) — no traffic for 30 min
- `ResumeProcessRestarted` (info) — uptime < 2 min
- `ResumeScrapeDown` (critical) — Prometheus can't scrape
- `ResumeBackupStale` (warning) — no backup in 48 h
- `ResumeDiskUsageHigh` (warning) — DB or photos > 1 GB

### 4. You want to verify from _outside_

Internal metrics say "I'm healthy from the inside." Synthetic monitoring
says "a real user can reach me from the outside." Three levels, pick
the one that matches how much you want to know.

- **Level 1** — Uptime Kuma or UptimeRobot pinging `/healthz` every
  60 s. 5 min to set up. Catches "site is down" and nothing else.
- **Level 2** — [`tests/synthetic/healthcheck.sh`](../tests/synthetic/healthcheck.sh)
  curls five key routes, asserts HTTP 200, response time < 2 s, and a
  route-specific body substring. 30 min to wire up a cron + webhook.
- **Level 3** — [`tests/synthetic/monitor.py`](../tests/synthetic/monitor.py)
  runs a headless chromium through the full user journey. Catches
  JS errors, missing images, CSS regressions. 1 h to wire up.

Setup walk-throughs below (§ "Setting up synthetic monitoring").

### 5. You're about to touch the code

See the development workflow in the next section. TL;DR: run
`manage.py query-audit` and `scripts/benchmark_routes.py` _first_, note
the numbers, touch the code, re-run, compare.

---

## Development workflow

### Before writing any new feature

1. **Pick the affected routes.** Write them down. You'll re-benchmark
   each one later.
2. **Capture the baseline.** If it's a route in `scripts/benchmark_routes.py`:
   ```bash
   python scripts/benchmark_routes.py 100 > baseline-before.md
   ```
   If it's a route not in the default set, add it to `ROUTES` at the
   top of that script first — the list is the source of truth for what
   we benchmark in CI.
3. **Note the current query count.** `query-audit` surfaces every
   cataloged query's execution plan:
   ```bash
   python manage.py query-audit | tee baseline-queries.txt
   ```
   If you're adding a new hot-path query, you must add it to
   `_AUDIT_QUERIES` in `manage.py`. An un-audited query is a future
   regression waiting to happen.
4. **Record the baseline in the PR description** once you open the PR.
   "Before: p95=21ms, 10 queries. After: p95=24ms, 10 queries. 14%
   slowdown attributed to new translation overlay; acceptable."

### During development

- Run `ruff check` and `bandit -ll` continuously (pre-commit handles
  this on every commit; on save in your editor is ideal).
- Write tests that follow [`tests/TESTING_STANDARDS.md`](../tests/TESTING_STANDARDS.md) —
  empty, boundary, Unicode, injection, concurrency.
- `pytest --cov=app -q` — covered-lines ratchet is 60% in CI; rising
  over time.
- `pytest -k fuzz` — Hypothesis property tests. Use
  `--hypothesis-seed=random` to catch flake-prone failures earlier.

### Before submitting a PR

1. **Re-benchmark** the affected routes.
   ```bash
   python scripts/benchmark_routes.py 100 > baseline-after.md
   diff baseline-before.md baseline-after.md
   ```
   Any regression beyond 50% or a new query gets discussed in the PR
   description.
2. **Mutation check** modified modules:
   ```bash
   python manage.py mutation-report  # wraps mutmut
   ```
   Survived mutants need either a new test or a justification in
   `tests/mutation_review.md`.
3. **Locust smoke** (optional but recommended for route changes):
   ```bash
   locust -f tests/loadtests/locustfile.py --headless -u 20 -r 5 -t 30s \
       --host http://localhost:8080
   ```
   Zero 500s, no latency spike in the tail.
4. **Re-run `query-audit`** and confirm no new full-table scans snuck in.
5. **Targeted edge-case tests** — `pytest tests/test_edge_cases_<area>.py`
   for the affected domain.

### CI will automatically

1. Run the full test suite with coverage ratchet
2. Run ruff + bandit + vulture + SQL-grep guard
3. Build the container image + Trivy CVE scan
4. Publish + cosign-sign if everything passes on `main`

If a CI check fails, don't `--no-verify` around it — fix the
underlying issue. Pre-commit hooks exist for a reason.

### After deploying to production

- Watch `/metrics` for 30 minutes after the deploy. An error-rate
  spike right after a deploy is usually the deploy. Roll back fast.
- Watch the Grafana dashboard's "Error rate by category" and "Request
  latency" panels — these two combined surface most regressions.
- Check the admin dashboard's "System Health" card (Phase 18.9 —
  shows per-category error totals since restart).
- If anything looks off, you have `X-Request-ID` on every response.
  Grab one from a broken user request and trace it through structured
  logs (see §1 above).

---

## Setting up Prometheus + Grafana

Estimated time: **15 minutes** if you already have a Prometheus/Grafana
stack running. **45 minutes** from scratch.

### Step 1 — Turn on `/metrics` inside resume-site

Log into the admin panel, go to Settings → Security, and set:

- `metrics_enabled = true`
- `metrics_allowed_networks = <your Prometheus host CIDR>`

Empty `metrics_allowed_networks` falls back to admin
`allowed_networks` from `config.yaml`. Disallowed clients get 404
(same "does this exist?" ambiguity as the `metrics_enabled=false`
case).

### Step 2 — Add resume-site as a Prometheus scrape target

`prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

rule_files:
  - alerting-rules.yaml    # drop docs/alerting-rules.yaml next to this file

scrape_configs:
  - job_name: resume-site
    metrics_path: /metrics
    static_configs:
      - targets: ['your-resume-site-host:8080']
```

Reload with `curl -XPOST http://prometheus:9090/-/reload`.

### Step 3 — Minimal compose snippet (optional)

If you don't already have a monitoring stack, drop this alongside
your `compose.yaml`:

```yaml
# monitoring-compose.yaml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alerting-rules.yaml:/etc/prometheus/alerting-rules.yaml:ro
      - prom-data:/prometheus
    ports: ['9090:9090']
    networks: [monitoring]

  grafana:
    image: grafana/grafana:latest
    environment:
      GF_SECURITY_ADMIN_PASSWORD: changeme
    volumes:
      - grafana-data:/var/lib/grafana
    ports: ['3000:3000']
    networks: [monitoring]

  alertmanager:
    image: prom/alertmanager:latest
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    ports: ['9093:9093']
    networks: [monitoring]

volumes:
  prom-data:
  grafana-data:

networks:
  monitoring:
    external: true      # shared with resume-site's own compose network
```

### Step 4 — Import the Grafana dashboard

1. Open Grafana → Dashboards → New → Import.
2. Upload [`docs/grafana-dashboard.json`](./grafana-dashboard.json).
3. When prompted, select your Prometheus data source.
4. Save.

The dashboard's 11 panels are documented inline (each has a
`description` field visible in the panel info icon).

### Step 5 — Wire Alertmanager to your channel

Alertmanager config maps alert `severity` / `component` labels to
receivers. Minimum viable config:

```yaml
# alertmanager.yml
route:
  receiver: default
  group_by: [alertname, component]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers: [severity="critical"]
      receiver: pager
    - matchers: [severity="warning"]
      receiver: chat
    - matchers: [severity="info"]
      receiver: chat
      repeat_interval: 24h

receivers:
  - name: default
    webhook_configs:
      - url: https://your-chat-webhook
  - name: pager
    # PagerDuty / OpsGenie / email — pick one
    pagerduty_configs:
      - routing_key: <your key>
  - name: chat
    slack_configs:
      - api_url: <your slack webhook>
        channel: '#resume-site-alerts'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}\n{{ .Annotations.description }}\nRunbook: {{ .Annotations.runbook_url }}\n{{ end }}'
```

The `{{ .Annotations.runbook_url }}` template is the critical link to
`alerting-rules.md` — don't drop it from your template.

---

## Setting up synthetic monitoring

### Level 1 — Uptime ping (5 min)

Sign up for [Uptime Kuma](https://uptime.kuma.pet/) (self-hosted) or
[UptimeRobot](https://uptimerobot.com/) (free tier). Add one monitor:

- URL: `https://your-domain/healthz`
- Method: GET
- Expected status: 200
- Interval: 60 s
- Alert to: whichever notification channel you use

Done. This catches "site is down" and nothing else. You want at least
this.

### Level 2 — Key-page curl probe (30 min)

Copy [`tests/synthetic/healthcheck.sh`](../tests/synthetic/healthcheck.sh)
to the monitoring host (NOT the app host — the probe is external by
design). Required env vars:

```bash
export RESUME_BASE_URL=https://your-domain
export RESUME_WEBHOOK_URL=https://your-chat-webhook    # optional
```

Run once manually:

```bash
bash tests/synthetic/healthcheck.sh
```

Expected output: `summary: 5/5 routes passed` and exit 0.

**Wire as a cron (every 60 s, serialised so slow probes don't stack):**

```cron
* * * * * flock -n /tmp/resume-healthcheck.lock \
  RESUME_BASE_URL=https://your-domain \
  /opt/resume-site/tests/synthetic/healthcheck.sh >/dev/null 2>&1
```

**Or as a systemd timer** (preferred on Fedora / RHEL hosts):

```ini
# /etc/systemd/system/resume-healthcheck.service
[Unit]
Description=resume-site synthetic healthcheck

[Service]
Type=oneshot
Environment=RESUME_BASE_URL=https://your-domain
Environment=RESUME_WEBHOOK_URL=https://your-chat-webhook
ExecStart=/opt/resume-site/tests/synthetic/healthcheck.sh

# /etc/systemd/system/resume-healthcheck.timer
[Unit]
Description=resume-site healthcheck every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5s

[Install]
WantedBy=timers.target
```

Activate with `systemctl enable --now resume-healthcheck.timer`.

### Level 3 — Playwright user journey (1 hour)

Install Playwright on the monitoring host (NOT the app host):

```bash
python3 -m venv /opt/resume-monitor/.venv
/opt/resume-monitor/.venv/bin/pip install playwright
/opt/resume-monitor/.venv/bin/playwright install chromium
```

Run the monitor manually:

```bash
RESUME_BASE_URL=https://your-domain \
  /opt/resume-monitor/.venv/bin/python \
  /opt/resume-site/tests/synthetic/monitor.py
```

The script visits five checkpoints — landing, portfolio, blog, contact
(honeypot-flagged), admin login — and captures a screenshot under
`/tmp/resume-monitor/` on any failure. The JSON output lists each
step's duration and detail.

**Wire as a cron (every 15 min, serialised):**

```cron
*/15 * * * * flock -n /tmp/resume-monitor.lock \
  bash -c 'RESUME_BASE_URL=https://your-domain \
           RESUME_WEBHOOK_URL=https://your-chat-webhook \
           /opt/resume-monitor/.venv/bin/python \
           /opt/resume-site/tests/synthetic/monitor.py'
```

### A note on the honeypot

The Level 3 script submits the contact form with the honeypot field
populated so the submission is flagged spam server-side and never
reaches the admin inbox. This keeps monitoring traffic out of real
operator queues. If the honeypot field in
`templates/public/contact.html` is ever renamed, update the
`_HONEYPOT_SELECTOR` constant at the top of `monitor.py` to match —
otherwise the monitor starts creating real contact submissions.

---

## Troubleshooting checklist

When something is wrong and you don't know where to start:

| Symptom | First place to look |
|---|---|
| Users report "site is slow" | Grafana → "Request latency" panel; if p95 spikes, `query-audit` |
| Users report 500 errors | Structured logs filtered to `level=ERROR`; trace by `request_id` |
| Uptime Kuma paging | `curl https://your-domain/readyz` — the detail field names the failed check |
| Alerts firing in bursts then clearing | Looks like a restart loop — check `ResumeProcessRestarted` + container logs |
| Dashboard says "no data" | `metrics_enabled=true`? Scraper IP in `metrics_allowed_networks`? Try `curl /metrics` from the scraper |
| "Something got slower" but you can't tell what | Run `scripts/benchmark_routes.py 100` and compare to the baseline in `PERFORMANCE.md` |
| Deploy went out, now errors | Error-rate spike right after deploy = the deploy. Roll back; investigate after |
| Can't reproduce locally | Grab the `X-Request-ID` from the broken response; trace end-to-end in JSON logs; replay the method / path / payload via `curl` |

---

## Extending this runbook

Adding a new observability tool to the project? Write its entry here
_at the same time_ as you ship the tool. A tool undocumented in this
file is, operationally, a tool that doesn't exist — no one will
remember to use it at 3 AM during an incident.

Write the section with the same structure as the entries above: what
it answers, when to reach for it, exact invocation, and what to do
with the output. Link runbook entries for each alert you add in
[`alerting-rules.md`](./alerting-rules.md); link back from here so
operators can navigate between the two.
