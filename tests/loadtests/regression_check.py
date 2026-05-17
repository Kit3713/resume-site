"""Phase 32 — Load-test regression checker.

Parses the locust ``--csv`` stats output and compares each tracked
endpoint's measured p95 against the corresponding threshold in
``thresholds.json``. Exits 1 (with a human-readable summary) if any
endpoint exceeds threshold * 1.20 — the +20% headroom is the
established gate from the v0.3.0 Phase 18.6 design doc.

Used by the ``perf-regression`` CI job (.github/workflows/ci.yml).
Can also be invoked locally:

    locust ... --csv=run_stats
    python tests/loadtests/regression_check.py \\
        --stats run_stats_stats.csv \\
        --thresholds tests/loadtests/thresholds.json

The CSV format is locust's standard ``*_stats.csv`` output:

    Type,Name,Request Count,Failure Count,...,95%,98%,99%,99.9%,99.99%,100%

The 95% column carries the milliseconds value we compare against.

Design notes:
* Endpoints absent from ``thresholds.json`` are skipped (printed as
  ``SKIPPED`` in the summary). New routes start advisory until a
  threshold is committed.
* The ``Aggregated`` row is never compared — only per-endpoint p95s
  are meaningful for a regression gate.
* Failure-count > 0 on any tracked endpoint is a hard failure
  regardless of latency. A 500 is always a regression.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Multiplier applied to the threshold before comparing. Matches the
# >20% gate documented in PERFORMANCE.md and the v0.3.0 Phase 18.6
# carry-over. Keep this in sync with the prose threshold in
# PERFORMANCE.md and the perf-regression CI job description.
GATE_MULTIPLIER = 1.20


def _parse_p95(raw: str) -> float | None:
    """Return the float p95 value, or None if locust emitted N/A."""
    raw = (raw or '').strip()
    if not raw or raw.upper() in {'N/A', 'NAN'}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_stats(stats_csv: Path) -> dict[str, dict]:
    """Load per-endpoint p95 + failure count from a locust stats CSV.

    Returns a mapping of ``name -> {"p95_ms": float|None, "failures": int,
    "requests": int}``. The ``Aggregated`` row is dropped.
    """
    rows: dict[str, dict] = {}
    with stats_csv.open(newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get('Name') or '').strip()
            if not name or name == 'Aggregated':
                continue
            rows[name] = {
                'p95_ms': _parse_p95(row.get('95%', '')),
                'failures': int(row.get('Failure Count', '0') or 0),
                'requests': int(row.get('Request Count', '0') or 0),
            }
    return rows


def load_thresholds(path: Path) -> dict[str, float]:
    """Load the {endpoint: p95_ms} map from thresholds.json.

    Entries with a null p95_ms are excluded — they mean 'not yet
    baselined; skip the gate'.
    """
    raw = json.loads(path.read_text(encoding='utf-8'))
    out: dict[str, float] = {}
    for endpoint, spec in raw.get('thresholds', {}).items():
        value = spec.get('p95_ms') if isinstance(spec, dict) else None
        if value is None:
            continue
        out[endpoint] = float(value)
    return out


def compare(
    measured: dict[str, dict],
    thresholds: dict[str, float],
    gate: float = GATE_MULTIPLIER,
) -> tuple[list[dict], list[dict], list[str]]:
    """Compare measured p95 against thresholds.

    Returns ``(failures, passes, skipped)`` where each list element is a
    dict with the columns the report renders. ``skipped`` is a list of
    endpoint names (no measurement available).
    """
    failures: list[dict] = []
    passes: list[dict] = []
    skipped: list[str] = []
    for endpoint, threshold in sorted(thresholds.items()):
        stats = measured.get(endpoint)
        if stats is None or stats['p95_ms'] is None:
            skipped.append(endpoint)
            continue
        gate_value = threshold * gate
        record = {
            'endpoint': endpoint,
            'p95_ms': stats['p95_ms'],
            'threshold_ms': threshold,
            'gate_ms': gate_value,
            'failures': stats['failures'],
            'requests': stats['requests'],
        }
        # A failed request is always a regression — latency on top of
        # a 500 is meaningless. Treat any non-zero failure count on a
        # tracked endpoint as a hard fail.
        if stats['failures'] > 0 or stats['p95_ms'] > gate_value:
            failures.append(record)
        else:
            passes.append(record)
    return failures, passes, skipped


def render_summary(
    failures: list[dict],
    passes: list[dict],
    skipped: list[str],
    *,
    gate: float = GATE_MULTIPLIER,
) -> str:
    """Render the markdown-friendly summary table for the CI log."""
    lines: list[str] = []
    lines.append('## Load-Test Regression Summary')
    lines.append('')
    lines.append(f'Gate multiplier: {gate:.2f}x of threshold p95.')
    lines.append('')
    lines.append(
        '| Endpoint | Measured p95 (ms) | Threshold (ms) | Gate (ms) | Reqs | Fails | Verdict |'
    )
    lines.append('|---|---:|---:|---:|---:|---:|---|')
    # Emit failures first so a reviewer scanning the table sees the
    # regressions without scrolling. The verdict comes from the list
    # the record belongs to — no per-record set membership check.
    for verdict, bucket in (('FAIL', failures), ('PASS', passes)):
        for record in bucket:
            lines.append(
                '| {endpoint} | {p95:.1f} | {threshold:.1f} | {gate_ms:.1f} | '
                '{reqs} | {fails} | {verdict} |'.format(
                    endpoint=record['endpoint'],
                    p95=record['p95_ms'],
                    threshold=record['threshold_ms'],
                    gate_ms=record['gate_ms'],
                    reqs=record['requests'],
                    fails=record['failures'],
                    verdict=verdict,
                )
            )
    if skipped:
        lines.append('')
        lines.append('Skipped (no measurement available): ' + ', '.join(sorted(skipped)))
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--stats',
        required=True,
        type=Path,
        help='Path to the locust *_stats.csv file.',
    )
    parser.add_argument(
        '--thresholds',
        default=Path('tests/loadtests/thresholds.json'),
        type=Path,
        help='Path to thresholds.json (default: tests/loadtests/thresholds.json).',
    )
    parser.add_argument(
        '--gate',
        type=float,
        default=GATE_MULTIPLIER,
        help=f'Gate multiplier (default: {GATE_MULTIPLIER}).',
    )
    args = parser.parse_args(argv)

    if not args.stats.is_file():
        print(f'ERROR: stats CSV not found: {args.stats}', file=sys.stderr)
        return 2
    if not args.thresholds.is_file():
        print(f'ERROR: thresholds.json not found: {args.thresholds}', file=sys.stderr)
        return 2

    measured = load_stats(args.stats)
    thresholds = load_thresholds(args.thresholds)
    if not thresholds:
        print('ERROR: no usable thresholds (all values null?).', file=sys.stderr)
        return 2

    failures, passes, skipped = compare(measured, thresholds, gate=args.gate)
    print(render_summary(failures, passes, skipped, gate=args.gate))

    if failures:
        print('', file=sys.stderr)
        print(f'REGRESSION: {len(failures)} endpoint(s) failed the gate.', file=sys.stderr)
        return 1

    print('')
    print(f'OK: {len(passes)} endpoint(s) within threshold; {len(skipped)} skipped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
