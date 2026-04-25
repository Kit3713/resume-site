#!/usr/bin/env python3
"""Hot-path route benchmark (Phase 12.2).

Measures wall-clock time, SQL query count, and response size for the five
highest-traffic public routes against an in-process Flask test client.

Usage:
    python scripts/benchmark_routes.py [iterations]

Output:
    Prints a markdown table suitable for pasting into PERFORMANCE.md.

Notes:
    * Uses the app's existing test-config fixture layout (a temp DB seeded
      with a realistic content volume).
    * SQL trace callback counts every statement sqlite3 sends to the driver.
      That's the number Phase 12.1 indexes and cache layer are supposed to
      keep stable across releases.
    * The first request per route is not timed (warm-up).
    * The script defaults `RESUME_SITE_LOG_LEVEL=WARNING` so timings aren't
      polluted by the stderr log sink; export the variable explicitly to
      override (e.g. `RESUME_SITE_LOG_LEVEL=DEBUG` for diagnostics).
"""

from __future__ import annotations

import os

# Default the app log level to WARNING before importing app code so
# benchmarks don't measure the stderr-sink overhead. setdefault honours
# an operator override from the shell.
os.environ.setdefault('RESUME_SITE_LOG_LEVEL', 'WARNING')

import sqlite3  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.db import _apply_pragmas  # noqa: E402

ROUTES = [
    ('landing', '/'),
    ('portfolio', '/portfolio'),
    ('blog_index', '/blog'),
    ('blog_post', '/blog/seed-post-1'),
    ('contact', '/contact'),
]

ITERATIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 50


def _write_test_config(tmp_path: Path) -> Path:
    """Mirror tests/conftest.py _write_test_config for benchmark isolation."""
    db_path = tmp_path / 'bench.db'
    photos_path = tmp_path / 'photos'
    photos_path.mkdir(exist_ok=True)
    pw_hash = (
        'pbkdf2:sha256:600000$bngNDaCGXphoecmK$'
        '7e35934ae555af4c418e1399fa0c866411b05f64bf8c3ef64d50c93990a7497b'
    )
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        'secret_key: "benchmark-key-32-chars-or-more-aaaaaaaa"\n'
        f'database_path: "{db_path}"\n'
        f'photo_storage: "{photos_path}"\n'
        'session_cookie_secure: false\n'
        'admin:\n'
        '  username: "admin"\n'
        f'  password_hash: "{pw_hash}"\n'
        '  allowed_networks:\n'
        '    - "127.0.0.0/8"\n'
    )
    return config_path


def _init_db(db_path: Path) -> None:
    """Initialize schema + migrations + seed data."""
    project_root = Path(__file__).resolve().parent.parent
    schema = (project_root / 'schema.sql').read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        for migration in sorted((project_root / 'migrations').glob('*.sql')):
            conn.executescript(migration.read_text())
        conn.commit()

        # Seed: turn the blog on and create content the routes will hit.
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('blog_enabled', 'true')")
        for i in range(1, 21):
            conn.execute(
                'INSERT INTO blog_posts '
                '(slug, title, summary, content, content_format, status, '
                'published_at, reading_time) '
                "VALUES (?, ?, ?, ?, 'html', 'published', datetime('now'), 3)",
                (
                    f'seed-post-{i}',
                    f'Seed Post {i}',
                    'Benchmark seed post.',
                    f'<p>Body of post {i}. ' + 'Lorem ipsum ' * 50 + '</p>',
                ),
            )
        # A handful of photos so the portfolio route has real work to do.
        for i in range(1, 11):
            conn.execute(
                'INSERT INTO photos '
                '(filename, storage_name, mime_type, width, height, '
                'file_size, title, display_tier, sort_order) '
                "VALUES (?, ?, 'image/jpeg', 800, 600, 50000, ?, 'grid', ?)",
                (f'seed-{i}.jpg', f'seed-{i}.jpg', f'Seed Photo {i}', i),
            )
        conn.commit()
    finally:
        conn.close()


def _make_tracing_connect(real_connect, queries_list):
    """Build a sqlite3.connect replacement that traces queries into `queries_list`.

    Pulled out of the benchmark loop so we don't capture loop-local bindings
    inside a closure (ruff B023).
    """

    def _tracing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(queries_list.append)
        _apply_pragmas(conn)
        return conn

    return _tracing_connect


def _benchmark_route(client, path: str) -> dict:
    """Run `ITERATIONS` requests; return timing + query-count stats."""
    query_counts: list[int] = []
    response_times_ms: list[float] = []
    status_codes: Counter[int] = Counter()
    response_size = 0

    # Warm-up (not timed) — amortizes first-hit overheads like template
    # compilation and Python bytecode caching.
    client.get(path)

    for _ in range(ITERATIONS):
        queries_this_request: list[str] = []

        # Wrap this iteration's DB connections with a trace callback to
        # count the queries. We do this by monkey-patching sqlite3.connect
        # for the duration of the request.
        real_connect = sqlite3.connect
        sqlite3.connect = _make_tracing_connect(real_connect, queries_this_request)
        try:
            t0 = time.perf_counter()
            resp = client.get(path)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        finally:
            sqlite3.connect = real_connect

        # The trace callback fires for PRAGMA setup too — filter those out
        # since they're infrastructure, not app-level queries.
        app_queries = [
            q for q in queries_this_request if not q.strip().upper().startswith('PRAGMA')
        ]
        query_counts.append(len(app_queries))
        response_times_ms.append(elapsed_ms)
        status_codes[resp.status_code] += 1
        response_size = len(resp.data)

    # Quick sanity check — if the benchmark hit a 500, the numbers aren't meaningful.
    if any(code >= 500 for code in status_codes):
        raise RuntimeError(f'{path}: got server errors: {dict(status_codes)!r}')

    return {
        'path': path,
        'iterations': ITERATIONS,
        'p50_ms': statistics.median(response_times_ms),
        'p95_ms': statistics.quantiles(response_times_ms, n=20)[18],
        'queries_median': int(statistics.median(query_counts)),
        'queries_max': max(query_counts),
        'response_bytes': response_size,
        'status_modal': status_codes.most_common(1)[0][0],
    }


def main() -> int:
    print(
        f'effective RESUME_SITE_LOG_LEVEL={os.environ["RESUME_SITE_LOG_LEVEL"]}',
        file=sys.stderr,
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        config_path = _write_test_config(tmp_path)
        _init_db(tmp_path / 'bench.db')

        app = create_app(str(config_path))
        app.config['TESTING'] = True

        results = []
        with app.test_client() as client:
            for name, path in ROUTES:
                print(f'  benchmarking {name:12s} ({path})', file=sys.stderr)
                r = _benchmark_route(client, path)
                r['name'] = name
                results.append(r)

        # Render a markdown table for PERFORMANCE.md.
        print()
        print('| Route | Path | p50 (ms) | p95 (ms) | Queries | Bytes |')
        print('| ----- | ---- | -------: | -------: | ------: | ----: |')
        for r in results:
            print(
                f'| {r["name"]} | `{r["path"]}` '
                f'| {r["p50_ms"]:.2f} | {r["p95_ms"]:.2f} '
                f'| {r["queries_median"]} | {r["response_bytes"]:,} |'
            )
        print()
        print(f'_Measured over {ITERATIONS} iterations after one warm-up request._')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
