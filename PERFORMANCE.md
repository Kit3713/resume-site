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

### Baseline (to be recorded after v0.3.0 stabilization)

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

| Metric | Value |
|---|---|
| Cold start to first 200 OK | — (to be measured) |
| Image size (amd64) | — |
| Image size (arm64) | — |

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
