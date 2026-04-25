# Alerting Rules — Operator Runbook

**Companion to:** [`alerting-rules.yaml`](./alerting-rules.yaml)
**Phase:** 18.10 of [`ROADMAP_v0.3.0.md`](../ROADMAP_v0.3.0.md)

This file is the runbook for every alert shipped in
`alerting-rules.yaml`. Each `runbook_url` annotation in the YAML points at
a section here. When an alert fires, this is the page to read — it
explains what the alert means, what could cause it, and what to do
about it in the order of cost and reversibility.

The threshold values in the YAML are starting points. Tune them to your
deployment after a week of normal traffic. Thresholds are the one thing
you're expected to change in `alerting-rules.yaml`; rule expressions
should not need edits because the metric names are tested against the
registry (see `tests/test_alerting_rules.py`).

---

## Setup

Minimal Prometheus configuration:

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

rule_files:
  - alerting-rules.yaml

scrape_configs:
  - job_name: resume-site
    static_configs:
      - targets: ['resume-site:8080']
    # /metrics is gated — set metrics_enabled=true in the admin panel
    # and add the Prometheus IP to metrics_allowed_networks.
```

Reload with `curl -XPOST http://prometheus:9090/-/reload` or `SIGHUP` —
no restart needed.

Every alert has three labels you can route on:

| Label       | Values                                                 |
|-------------|--------------------------------------------------------|
| `severity`  | `critical`, `warning`, `info`                          |
| `component` | `application`, `security`, `performance`, `traffic`, `availability` |

---

## Severity taxonomy

- **critical** — an operator should look at this within minutes. A user
  is already seeing something broken, or is about to. Page on these.
- **warning** — something is off that needs investigation inside a
  normal working window. Not urgent, not negligible.
- **info** — diagnostic. You probably want these in a chat channel, not
  an on-call pager.

---

## ResumeInternalErrorRate

**Severity:** critical • **Component:** application

**What it means:** the `errorhandler(Exception)` in `app/__init__.py` has
categorised one or more raised exceptions as `InternalError`. That means
the exception isn't an `HTTPException`, a `DomainError`, a `sqlite3`
database error, or a network error — it's genuinely unexpected. A bug.

**What to check:**

1. **Find the request.** Tail structured logs for level=ERROR and
   error_category=InternalError. Every record has a `request_id` — note it.
2. **Re-read the traceback.** The same `request_id` appears on a
   separate ERROR record with the full `exc_info` traceback (logged by
   the handler before the finalised request log record).
3. **Reproduce.** Use the `path`, `method`, and `user_agent` fields to
   replicate the request locally.

**Mitigations** (in order of reversibility):

- Add an explicit exception class (ExternalError / DataError /
  ValidationError) and raise it at the right layer so the handler
  classifies it out of `InternalError`.
- If the bug is in a plugin or third-party dependency, pin a prior
  version until it's fixed.
- As a last resort, disable the route or feature via a settings toggle.

**Do not** silence the alert by lowering the threshold — an InternalError
rate floor of zero is the design.

---

## ResumeUpstreamErrorRate

**Severity:** warning • **Component:** availability

**What it means:** the application is emitting 502 / 503 / 504 status
codes (or `OSError(ECONNREFUSED)` is escaping a request handler) faster
than 0.05/sec (~3/min) averaged over 5 minutes. Issue #134 carved this
category out of `InternalError` so rolling restarts and brief readyz
windows don't page on-call. A sustained rate, however, is a genuine
upstream availability problem.

**Likely causes:**

- A reverse proxy (Caddy / nginx) is racing the application boot —
  scrape windows fall inside the gap between TCP listen and readyz
  flipping green.
- Worker pool is saturated; new requests queue past Gunicorn's backlog
  and the proxy times them out.
- An outbound dependency the request handler calls (SMTP relay, an
  upstream HTTP API) is refusing connections, and the application is
  surfacing that as a 503.
- Container restart loop — see ResumeProcessRestarted.

**What to check:**

1. Tail structured logs for `error_category=UpstreamError` over the
   same window — `path` and `status` distinguish gateway-imposed 502s
   from application-emitted 503s.
2. `podman logs caddy` (or your reverse proxy) — the proxy is the most
   likely emitter of a clustered 502/504 burst.
3. Cross-check ResumeProcessRestarted; if it's also firing, the root
   cause is restart-rate, not steady-state availability.
4. If `OSError(ECONNREFUSED)` features in the logs, confirm the named
   upstream (SMTP relay, DNS resolver) is reachable from the container
   network namespace.

**Mitigations:**

- Tune the reverse-proxy upstream timeout / health-check window so it
  doesn't race the boot path.
- Bump worker count or the Gunicorn backlog (`--backlog`) if the
  request rate genuinely exceeds capacity.
- For a transient upstream API outage, add a circuit-breaker (or the
  existing retry budget) so a refused connection doesn't surface as a
  user-visible 503.

---

## ResumeAuthErrorSpike

**Severity:** warning • **Component:** security

**What it means:** AuthError-class responses (401/403) are flowing faster
than 0.1/sec (~6/min) averaged over 5 minutes. Likely sources:

- Credential-stuffing or brute-force against `/admin/login`.
- IP-restriction violations (someone probing admin routes from outside
  `admin.allowed_networks`).
- Legitimate user locked out of their own admin after password change
  (check the `security.login_failed` event stream for
  `reason='locked'`).

**What to check:**

1. Scrape `/metrics` for the current `errors_total{category="AuthError"}`
   rate. Is it still elevated?
2. Inspect the `login_attempts` table:
   ```
   SELECT ip_hash, COUNT(*) AS n, MAX(created_at) AS last
   FROM login_attempts WHERE success = 0
   GROUP BY ip_hash ORDER BY n DESC LIMIT 10;
   ```
3. If one `ip_hash` dominates, the Phase 13.6 lockout is already
   rejecting them — nothing to do at the application layer. Consider a
   firewall / Cloudflare IP block if the volume is excessive.
4. If many `ip_hash` values each have a handful of failures, that's
   credential stuffing from a botnet. Consider temporarily raising the
   Flask-Limiter POST limit stricter (e.g. 2/min) or adding a CAPTCHA.

**Do not** disable the lockout as a way to silence this alert — the
lockout is exactly what's protecting you.

---

## ResumeBruteForce

**Severity:** warning • **Component:** security

**What it means:** `login_attempts_total{outcome="invalid"}` is
incrementing faster than ~6/min averaged over 5 minutes. A bot or
an attacker is guessing admin credentials at sustained volume.

**How this differs from `ResumeAuthErrorSpike`:** the sibling rule
watches every 401/403 anywhere in the app (failed API tokens,
IP-restriction rejections, admin lockout 429s). `ResumeBruteForce`
is narrower — only credential-mismatch events on the admin login
form. If both fire together, you're almost certainly looking at a
password-guessing campaign. If only the spike fires, investigate
the broader auth surface.

**What to check:**

1. Scrape `/metrics` and confirm `resume_site_login_attempts_total`
   is still incrementing for `outcome="invalid"`. A few minutes of
   elevated `outcome="locked"` increments usually follow — that's
   the Phase 13.6 lockout refusing the attacker after the threshold
   was hit.
2. Inspect `login_attempts` to see how many distinct `ip_hash`
   values are in play:
   ```
   SELECT ip_hash, COUNT(*) AS n, MAX(created_at) AS last
   FROM login_attempts WHERE success = 0
   GROUP BY ip_hash ORDER BY n DESC LIMIT 10;
   ```
3. If one hash dominates: the in-process lockout is already
   rejecting them. Consider an upstream block (Cloudflare, firewall,
   reverse-proxy rate-limit) if the noise is expensive.
4. If many hashes each contribute a few failures: that's
   credential-stuffing from a botnet. Reduce the Flask-Limiter POST
   budget on `/admin/login` (e.g. from 5/min to 2/min), or add a
   CAPTCHA, or move the admin surface behind Tailscale-only
   `allowed_networks`.

**Do not** silence this alert by raising the threshold without
investigating — the point is to surface sustained attacks early
enough to take upstream action.

---

## ResumeHighLatency

**Severity:** warning • **Component:** performance

**What it means:** p95 request duration is over 1 second, averaged over
5 minutes. Real users are seeing the site feel slow.

**What to check:**

1. `python manage.py query-audit` — confirms every documented hot query
   still uses an index. A schema change elsewhere may have dropped a
   planner optimisation.
2. Look at the `resume_site_request_duration_seconds` histogram broken
   down by `path` label. If one route dominates, that's the culprit.
3. Check photo upload traffic — those go through Pillow and are
   naturally slow. If a bot is POSTing uploads, the Phase 6 upload
   validations should be rejecting them, but a flood can still slow
   things down.
4. SQLite file size. If `data/site.db` is in the hundreds of MB and
   you're near a disk-cache-pressure threshold, queries that used to be
   hot may now hit disk on every read.

**Mitigations:**

- Add an index (and a new `query-audit` entry to lock it in).
- Trim `page_views` retention via `manage.py purge-analytics`.
- Review whether any new feature added a per-request N+1 pattern.

---

## ResumeHighRequestRate

**Severity:** info • **Component:** traffic

**What it means:** the app is serving more than 100 requests per
minute, averaged over 5 minutes. For a portfolio site that's
significant — likely either a viral moment or a scanner.

**What to check:**

1. Browse the `/metrics` endpoint. Look at `requests_total` broken down
   by `path`. A wide spread of paths with a dominant `path="<unmatched>"`
   label is scanner behaviour (probing for admin panels, WordPress
   plugins, etc.) — fine, not actionable.
2. A single legitimate path spiking is organic traffic. Celebrate.

**Do not** reflexively block — the app is built to handle this. The
alert exists so you can distinguish "woah, LinkedIn noticed me" from
"woah, something broke upstream and my site is getting all the traffic".

---

## ResumeNoTraffic

**Severity:** warning • **Component:** availability

**What it means:** Prometheus can still scrape `/metrics` (otherwise
this rule would be stale), but `resume_site_requests_total` is not
advancing. The app is alive; nothing is reaching it.

**Likely causes:**

- Reverse proxy (Caddy) is down or misconfigured.
- DNS is broken.
- A CDN in front is caching a 5xx response and not forwarding.
- Firewall rule change blocked inbound traffic.

**What to check:**

1. `curl https://your-domain/` from outside your network. Does it
   respond?
2. `podman logs caddy` (or your reverse proxy) — look for recent
   config-reload errors.
3. `dig your-domain A` — are you still resolving to the right IP?

---

## ResumeProcessRestarted

**Severity:** info • **Component:** availability

**What it means:** `uptime_seconds < 120` — the Gunicorn master or the
whole container restarted in the last two minutes.

**This is fine when:**

- You just deployed.
- systemd's `resume-site.container` unit restarted for a healthy
  reason.

**This is a problem when:**

- It wasn't a deploy. Check `podman inspect` for the restart reason,
  and container logs for the last line before the crash.
- It's firing repeatedly (crash loop). The alert's `for: 1m` will keep
  re-firing in that case. Escalate to ResumeInternalErrorRate — the
  crash loop's root cause is almost certainly a hot-path exception.

---

## ResumeScrapeDown

**Severity:** critical • **Component:** availability

**What it means:** Prometheus's own `up{job="resume-site"}` gauge is
zero. Prometheus tried to scrape `/metrics` and got no answer.

**Likely causes (in decreasing order of frequency):**

1. `metrics_enabled` setting was toggled off.
2. The Prometheus scraper's IP fell outside `metrics_allowed_networks`
   (or the admin `allowed_networks` fallback).
3. The app is crashed or not running.
4. A network rule between Prometheus and the app is dropping the
   request.

**What to check:**

1. From the Prometheus host:
   `curl -v http://resume-site:8080/metrics`. A `404` means the feature
   flag is off. A `timeout` means network or process death.
2. If 404, set `metrics_enabled=true` in the admin panel and confirm
   `metrics_allowed_networks` includes the scraper IP (or leave it empty
   to inherit admin's allowed_networks).
3. If timeout, check `podman ps`. If the container is Exited, see
   ResumeProcessRestarted + container logs.

---

## ResumeBackupStale

**Severity:** warning - **Component:** backup

**What it means:** The `backup_last_success` setting timestamp is more than
48 hours old. The scheduled backup pipeline has stopped producing archives.

**Likely causes:**

- The `resume-site-backup.timer` systemd unit is disabled or failed.
- The backup volume mount is broken (container can't write to
  `/app/backups`).
- `manage.py backup` is erroring out (disk full, permission denied, DB
  locked for too long).

**What to check:**

1. `systemctl --user status resume-site-backup.timer` — is it active?
   When did it last trigger?
2. `podman exec resume-site python manage.py backup --list` — does a
   recent archive exist?
3. Run a manual backup to see the error:
   `podman exec resume-site python manage.py backup`
4. Check the backup volume:
   `podman volume inspect resume-site-backups`

**Mitigations:**

- Re-enable the timer: `systemctl --user enable --now resume-site-backup.timer`
- Fix volume permissions: `podman unshare chown 1000:1000 $(podman volume inspect --format '{{.Mountpoint}}' resume-site-backups)`
- If disk is full, prune old backups:
  `podman exec resume-site python manage.py backup --prune --keep 3`

---

## ResumeDiskUsageHigh

**Severity:** warning - **Component:** storage

**What it means:** One of the monitored storage paths (database file or
photo directory) exceeds 1 GB. Default threshold — tune to your deployment.

**What to check:**

1. `podman exec resume-site du -sh /app/data/site.db /app/photos/` to
   identify which path is growing.
2. For the database: the `page_views` table grows unbounded on active
   sites. Run `SELECT COUNT(*) FROM page_views;` and consider archiving
   older entries.
3. For photos: check whether unused uploads (deleted from the UI but not
   from disk) are accumulating.

**Mitigations:**

- Trim analytics: `podman exec resume-site python manage.py purge-analytics --older-than 90d`
- Remove orphaned photo files (photos deleted in the admin UI but left on
  disk due to a failed cleanup).
- Increase the alert threshold if your deployment legitimately serves
  large media.

---

## Extending this file

Adding a new alert rule:

1. Reference only metrics declared in `app/services/metrics.py`.
   `tests/test_alerting_rules.py` enforces this.
2. Give the rule a `severity` and `component` label from the taxonomy
   above.
3. Write a section here with the same heading as the alert name
   (anchors match on lower-cased alert names) and set the
   `runbook_url` annotation to `./alerting-rules.md#<alert-name>`.
4. Prefer adding a new group over bloating an existing one — operators
   can disable a whole group in one line if a rule turns out to be too
   noisy.
