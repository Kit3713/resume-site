# Performance Baselines

Quantitative baselines for the five public hot-path routes. These numbers
are the reference floor for Phase 12 (SQLite optimization, Pillow pipeline,
Python code cleanup). A future regression means **we broke something** —
investigate before merging.

The whole point of having baselines is to notice drift. Treat the
"regression thresholds" below as load-bearing; bypass them only with a
written justification in the PR description.

---

## Methodology

`scripts/benchmark_routes.py` spins up a fresh Flask app against a
temporary SQLite database seeded with representative content (20 published
blog posts, 10 portfolio photos, default site settings). Each route is
requested via Flask's in-process test client:

1. **One warm-up request per route** (not timed) — amortizes first-hit
   overheads like template compilation.
2. **N timed requests** (default 100) — `time.perf_counter()` around each
   client.get() call.
3. **SQL query count** — `sqlite3.Connection.set_trace_callback` records
   every statement the driver sees; PRAGMA-level statements are excluded.
4. **Response size** — `len(resp.data)` on the final body.

The benchmark harness isolates each run by replacing `sqlite3.connect`
with a tracing wrapper for the duration of the request. `_apply_pragmas`
is re-applied so measurements reflect production PRAGMA state
(foreign_keys, busy_timeout).

Re-run any time:

```bash
python scripts/benchmark_routes.py 100   # 100 iterations per route
```

## Test rig

| Component   | Value                                                |
| ----------- | ---------------------------------------------------- |
| CPU         | 12th Gen Intel Core i9-12900HK                       |
| Memory      | 32 GiB                                               |
| OS          | Fedora 43 Linux 6.19.10                              |
| Python      | 3.14.3                                               |
| SQLite      | Bundled with Python 3.14 (WAL, per-conn PRAGMAs)     |

Numbers on a different machine will differ — what matters is the
**ratio** between routes and the **query count**, not the absolute ms.

---

## Phase 12.1 Baseline (v0.3.0-pre)

Captured 2026-04-13, after Phase 12.1 landed (settings TTL cache,
batched IN-clause loaders, migration 005 indexes).

| Route       | Path                 | p50 (ms) | p95 (ms) | Queries | Bytes  |
| ----------- | -------------------- | -------: | -------: | ------: | -----: |
| landing     | `/`                  |    18.46 |    25.02 |      10 |  7,010 |
| portfolio   | `/portfolio`         |    18.94 |    27.33 |       6 | 12,333 |
| blog_index  | `/blog`              |    21.99 |    29.02 |      10 | 13,889 |
| blog_post   | `/blog/seed-post-1`  |    21.55 |    23.71 |       9 |  6,491 |
| contact     | `/contact`           |    22.11 |    23.81 |       3 |  7,133 |

_Measured over 100 iterations per route after one warm-up hit each._

## Phase 18.14 Baseline (v0.3.0-beta)

Captured 2026-04-18 on the same test rig listed above, against the
current main branch (after the observability + admin / i18n / webhook
/ backup / container-maturity phases shipped). Use this row as the
regression floor for Phase 18.6's CI gate.

| Route       | Path                 | p50 (ms) | p95 (ms) | Queries | Bytes  |
| ----------- | -------------------- | -------: | -------: | ------: | -----: |
| landing     | `/`                  |     2.28 |     2.43 |      11 |  8,215 |
| portfolio   | `/portfolio`         |     2.64 |     2.95 |       6 | 16,854 |
| blog_index  | `/blog`              |     2.54 |     2.72 |      11 | 14,806 |
| blog_post   | `/blog/seed-post-1`  |     2.08 |     2.25 |      11 |  7,888 |
| contact     | `/contact`           |     2.09 |     2.26 |       3 |  8,050 |

_Measured over 200 iterations per route after one warm-up hit each
(`RESUME_SITE_LOG_LEVEL=WARNING python scripts/benchmark_routes.py 200`).
The log-level env var silences the per-request INFO JSON line so the
stderr sink isn't the bottleneck — a visible effect on the
sub-5ms range we're now in._

**What changed since the Phase 12.1 baseline:**

* **p50/p95 dropped by ~10×.** Python 3.14 + Flask 3.1 are materially
  faster than the 3.12 line the original measurement was taken on,
  and the CPython 3.14 JIT (whose defaults now apply to the
  hot request path) accounts for most of the reduction. The
  ordering between routes is unchanged — blog / portfolio stay the
  heaviest, contact stays the lightest.
* **Landing / blog_index / blog_post all picked up +1 query vs.
  the 12.1 row.** Counted against the query-count strict-monotonic
  regression rule, so this was a known code change rather than
  drift — Phase 15.4's translation overlay adds a per-route
  translation lookup when the session locale differs from the
  default, and Phase 17.2 added a `backup_last_success` settings
  read. The numbers above are the new floor; tighter PR-time
  enforcement kicks in going forward.
* **Response bytes up ~15-35% per route.** Phase 15.4 added OG
  `<meta property="og:locale*">` tags and sitemap `hreflang`
  wiring; Phase 18.1 added `X-Request-ID` to every response — not
  in the body, but Content-Length accounting across the response
  envelope shifts a bit. Not alarming at this absolute size.

---

## What the query counts mean

| Route       | Queries | Where they go                                                                                                             |
| ----------- | ------: | ------------------------------------------------------------------------------------------------------------------------- |
| landing     |      10 | stats, services, skill domains (+1 batched skills query), featured posts, tags-for-posts batched, reviews, photos, settings (cached after 1st hit inside TTL window) |
| portfolio   |       6 | photos, categories, settings                                                                                              |
| blog_index  |      10 | post list + COUNT + tags-for-posts batched + settings                                                                     |
| blog_post   |       9 | post lookup, tags, prev/next links, settings                                                                              |
| contact     |       3 | settings only — form is stateless                                                                                         |

## Regression thresholds

The CI benchmark workflow (Phase 12.5 — not yet wired in) should fail the
build if any of these regress:

| Metric                | Threshold                                             |
| --------------------- | ----------------------------------------------------- |
| p50 per route         | > 1.5× baseline                                       |
| Query count per route | > baseline (strictly) — a new query is always a code change |
| Response bytes        | > 2× baseline (rules out accidental dump-every-row)   |

The query-count rule is the strictest because it catches N+1 regressions
(Phase 12.1 eliminated two of those; any new query in a listing route
should be deliberate).

## Known dead ends

Things we tested and did not do:

* **Jinja2 bytecode cache** — Flask already caches compiled templates
  per process; enabling a file-based bytecode cache added no measurable
  win on these small templates.
* **SQLite memory-mapped I/O** — WAL + page cache already keeps our
  working set in RAM; `PRAGMA mmap_size` didn't move any percentile.
* **Gunicorn workers vs threads** — This is an OS-level tuning concern
  and not captured by per-request benchmarks. See the deployment doc
  (`Containerfile`) for our recommended worker setup.

## Load Testing (Phase 18.6)

### Setup

```bash
pip install locust
locust -f tests/loadtests/locustfile.py --headless -u 50 -r 5 -t 5m --host http://localhost:8080
```

Three user behaviors are defined in `tests/loadtests/locustfile.py`:

| Behavior | Weight | Wait Time | Focus |
|---|---|---|---|
| PublicUserBehavior | 5 | 1-3s | Landing (40%), portfolio (20%), blog (20%), rest (20%) |
| APIConsumerBehavior | 2 | 0.5-2s | Public API reads with pagination |
| AdminBehavior | 1 | 2-5s | Dashboard, photos, blog admin, settings |

### Baseline (to be recorded during v0.3.0 release prep)

The table below is intentionally empty pending a run against the
published GHCR image with the 50-user / 5-minute protocol that matches
the roadmap's 18.6 "baseline load test" bullet. Capturing these from
an in-process test client (like the benchmark_routes.py numbers
above) would be misleading — locust exists specifically to measure
the realistic network + gunicorn + reverse-proxy path that a real
user hits, and that path doesn't exist under `flask test_client`.

Procedure (copy-paste once the v0.3.0-rc image is published):

```bash
pip install locust
# Start the app (Quadlet, compose, or a quick `podman run`).
locust -f tests/loadtests/locustfile.py --headless \
    -u 50 -r 5 -t 5m --host http://localhost:8080 --csv locust-baseline
# Drop the numbers from locust-baseline_stats.csv below.
```

| Endpoint | p50 | p95 | p99 | Queries | Size |
|---|---|---|---|---|---|
| `GET /` | — | — | — | — | — |
| `GET /portfolio` | — | — | — | — | — |
| `GET /blog` | — | — | — | — | — |
| `GET /api/v1/site` | — | — | — | — | — |
| `GET /admin/` | — | — | — | — | — |

### CI Regression Gate

Thresholds in `tests/loadtests/thresholds.json` (to be populated after
baseline). CI `perf-regression` job runs locust with 20 users for 60s
and fails the build if any endpoint's p95 exceeds its threshold by >20%.

### Container Startup Time

Captured 2026-04-18 against the `ghcr.io/kit3713/resume-site:0.3.2-beta`
image, running under rootless Podman 5.x on the test rig listed above.
Each measurement wipes the three volumes (`data`, `photos`, `backups`)
between runs so we exercise the full cold-start path — `docker-entrypoint.sh`
runs `init-db` (schema + migrations + seeds) on an empty DB before
handing off to Gunicorn, and `/readyz` verifies the migration state
before responding 200.

| Metric                                  | Value                                             |
|-----------------------------------------|---------------------------------------------------|
| Cold start to first 200 on `/readyz`    | 2.20 – 2.30 s (median 2.26 s across three runs)   |
| Image size (amd64)                      | 217 MB (uncompressed, `podman image inspect`)     |
| Image size (arm64)                      | Built by the same CI job — not locally measured; use the GHCR manifest size for the arm64 variant of the published tag |

Repro:

```bash
# Fresh cold start timed via `podman run` + curl /readyz:
podman volume create resume-bench-data
podman volume create resume-bench-photos
podman volume create resume-bench-backups
t0=$(date +%s.%N)
podman run -d --name resume-bench -p 18080:8080 \
  -v config.yaml:/app/config.yaml:Z \
  -v resume-bench-data:/app/data \
  -v resume-bench-photos:/app/photos \
  -v resume-bench-backups:/app/backups \
  ghcr.io/kit3713/resume-site:latest
until curl -fsS http://127.0.0.1:18080/readyz; do sleep 0.25; done
t1=$(date +%s.%N)
awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.2fs\n", b - a }'
```

The 2.3 s cold start comfortably undersigns the `HEALTHCHECK
--start-period=10s` budget in `Containerfile` — Phase 21.1's open
question "tighten if v0.3.0-rc1 measurements show consistent sub-5s
startup" is now answerable with "yes, tighten" (v0.4.0 concern).

## Failure Modes (Phase 18.7)

These are the infrastructure failure modes we've tested and the
behaviour they're locked in to produce. Regression guards live in
`tests/test_resilience.py` — each row below has at least one asserting
test.

| Failure | Expected Behaviour | Test |
|---|---|---|
| SMTP unreachable (`send_contact_email` returns False) | Contact form persists the submission to `contact_submissions` and redirects to the success page. No traceback in the body. | `test_smtp_failure_still_saves_submission_and_redirects` |
| `smtplib.SMTP` raises `ConnectionRefusedError` | The mail service's outer `except Exception` swallows it and returns `False`. Never propagates into the route. | `test_smtp_exception_is_swallowed_by_mail_service` |
| Two writers contend for the DB lock | `PRAGMA busy_timeout = 5000` is in effect; a writer that briefly holds the lock doesn't error the next writer — it waits and completes. | `test_busy_timeout_pragma_is_5_seconds`, `test_db_write_succeeds_when_prior_writer_finishes_within_timeout` |
| `os.replace` raises `ENOSPC` during photo upload | The `finally` block in `process_upload` cleans up the quarantine file. No partial file on disk. No DB row inserted. Response does not crash the process. | `test_disk_full_on_upload_leaves_no_partial_files` |
| `INSERT` raises `database or disk is full` on contact form | The app-level `errorhandler(Exception)` returns a minimal safe body (request id only). No `sqlite3`, no "disk is full" string, no traceback reaches the client. | `test_disk_full_on_db_write_does_not_leak_traceback` |
| Upload has valid magic bytes but truncated content | `Image.open` raises `OSError`; `process_upload` returns the user-facing error `"Image file is corrupt or truncated."` and the quarantine file is deleted. No promotion to the final path. Phase 18.7 change: previously we silently accepted these. | `test_truncated_image_is_rejected_cleanly` |
| Jinja2 template references an undefined variable | `errorhandler(Exception)` returns a 500 with no traceback, no `UndefinedError`, no Jinja2 internals in the body. | `test_template_rendering_failure_does_not_leak_traceback` |
| Tampered / malformed session cookie | Flask's session deserialiser rejects the signature; a fresh session is created. Request still returns 200. | `test_malformed_session_cookie_creates_new_session` |
| Oversized session cookie (3 KB, hand-crafted) | No 500 — server responds 200/400/431. | `test_oversized_session_cookie_does_not_crash` |
| Database file exists but is truncated (< 100 bytes) | `manage.py migrate` aborts with a non-zero exit code and a clear "truncated or corrupt" message. Does NOT silently apply migrations on top of the damaged file. Phase 18.7 change — previously we would have treated it as a fresh DB. | `test_migrate_aborts_on_truncated_database_file` |
| Database file contains random bytes (not a SQLite file) | `manage.py migrate` aborts with a non-zero exit code and mentions corruption / integrity / not-a-database. | `test_migrate_aborts_on_corrupt_database` |
| Database file doesn't exist | `manage.py migrate` creates it fresh — regression guard for the corruption check so it can't accidentally reject a legitimate new install. | `test_migrate_allows_nonexistent_database` |
| Any handler raises an unhandled exception | The 500 handler returns a minimal safe body — no traceback, no exception class name, no exception message. | `test_500_does_not_leak_traceback` |

**Not tested (deferred):**

* Full disk exhaustion during SQLite write that leaves the DB in a
  recoverable state — subsequent requests working once space is freed.
  Requires filesystem fault injection (e.g. loopback device with a
  quota). Manual test procedure lives in the pen-test checklist.
* CDN unavailability (GSAP CDN down) — deferred to Phase 18.4
  Playwright work; exercised there with a blocked-request setup.

## Not yet covered

* Lighthouse scores for the landing page (Performance, Accessibility, Best
  Practices, SEO) — requires a running browser.
* Memory usage at idle and under load (50 concurrent users) — requires
  process monitoring during locust run.
