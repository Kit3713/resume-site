"""Phase 32 — Memory-leak probe wrapping a locust run.

Records the process RSS of the target Flask app at the start of a
locust run, again at the end, and reports the delta. WARNs if the
end-of-run RSS exceeds the start RSS by > 50%. **Advisory only in
v0.3.3** — the script exits 0 even when the warn threshold trips, so
CI does not fail on memory growth (yet). v0.4.0 may ratchet this to
blocking once the floor is stable across runners.

Usage::

    # Pass the PID of an already-running app (e.g. the CI container).
    python tests/loadtests/memory_probe.py --pid 1234 -- \\
        locust -f tests/loadtests/locustfile.py --headless \\
            -u 20 -t 60s --host http://localhost:8080 \\
            --csv=run_stats

Everything after ``--`` is the locust command to invoke. The probe:

1. Reads ``/proc/<pid>/status`` (VmRSS) before the locust process starts.
2. Runs the locust command (stdout + stderr passed through).
3. Reads ``/proc/<pid>/status`` again after locust exits.
4. Prints a single-line summary suitable for a CI log:

       MEMORY: pid=1234 start=58.4MiB end=61.1MiB delta=+2.7MiB (+4.6%)

Falls back gracefully when ``/proc`` is unavailable (e.g. macOS dev
environments) — the probe prints a warning and exits 0 without
running locust. CI is Linux-only so this only matters for local use.

The exit code is whatever locust returned. The probe never overrides
locust's exit code — a regression in latency must still fail the
build via ``regression_check.py``.
"""

from __future__ import annotations

import argparse
import subprocess  # noqa: S404 — invoked with explicit argv from the caller, no shell.
import sys
from pathlib import Path

# WARN threshold expressed as a multiplier of the starting RSS.
# A run that ends with > 1.5x the start RSS is flagged but not failed
# in v0.3.3. Tightened to blocking in v0.4.0 per ROADMAP_v0.3.3.md
# Phase 32 bullet 3.
WARN_RATIO = 1.50


def read_rss_bytes(pid: int) -> int | None:
    """Return the RSS of ``pid`` in bytes, or None if /proc is unavailable.

    Uses ``/proc/<pid>/status`` (line ``VmRSS:`` in kB). Returns None on
    any failure — the caller decides whether the probe is fatal.
    """
    status = Path(f'/proc/{pid}/status')
    if not status.is_file():
        return None
    try:
        for line in status.read_text(encoding='utf-8').splitlines():
            if line.startswith('VmRSS:'):
                parts = line.split()
                # Format: "VmRSS:    58432 kB"
                return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def format_bytes(n: int) -> str:
    """Render a byte count as MiB with one decimal."""
    return f'{n / (1024 * 1024):.1f}MiB'


def run_probe(pid: int, locust_argv: list[str]) -> int:
    """Run the locust command bracketed by RSS reads. Returns locust's exit."""
    start_rss = read_rss_bytes(pid)
    if start_rss is None:
        print(
            f'WARNING: /proc/{pid}/status unavailable; memory probe skipped.',
            file=sys.stderr,
        )
        # Still run the locust command so the rest of the pipeline works.
        return subprocess.call(locust_argv)  # noqa: S603 — argv from caller, no shell.

    print(f'MEMORY: pid={pid} start={format_bytes(start_rss)}', file=sys.stderr)
    # subprocess.call: argv comes from the caller, no shell, no untrusted
    # input. The locust binary lives on PATH from requirements-dev.
    rc = subprocess.call(locust_argv)  # noqa: S603

    end_rss = read_rss_bytes(pid)
    if end_rss is None:
        print(
            f'WARNING: end-RSS read failed for pid={pid}; probe inconclusive.',
            file=sys.stderr,
        )
        return rc

    delta = end_rss - start_rss
    pct = (end_rss / start_rss - 1.0) * 100.0 if start_rss > 0 else 0.0
    sign = '+' if delta >= 0 else '-'
    line = (
        f'MEMORY: pid={pid} start={format_bytes(start_rss)} '
        f'end={format_bytes(end_rss)} delta={sign}{format_bytes(abs(delta))} '
        f'({sign}{abs(pct):.1f}%)'
    )
    print(line)
    if start_rss > 0 and end_rss > start_rss * WARN_RATIO:
        print(
            f'WARNING: RSS grew >{(WARN_RATIO - 1.0) * 100.0:.0f}% over the run — '
            'possible memory leak. Advisory only in v0.3.3; investigate before '
            'v0.4.0 where this becomes a blocking gate.',
            file=sys.stderr,
        )
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--pid',
        type=int,
        required=True,
        help='PID of the app process to probe (NOT locust itself).',
    )
    parser.add_argument(
        'locust',
        nargs=argparse.REMAINDER,
        help='Locust command to execute (prefix with `--`).',
    )
    args = parser.parse_args(argv)
    locust_argv = list(args.locust)
    # Strip the leading "--" argparse leaves in REMAINDER.
    if locust_argv and locust_argv[0] == '--':
        locust_argv = locust_argv[1:]
    if not locust_argv:
        parser.error('locust command is required (after --)')
    return run_probe(args.pid, locust_argv)


if __name__ == '__main__':
    sys.exit(main())
