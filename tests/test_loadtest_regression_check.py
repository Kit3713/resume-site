"""Phase 32 — unit tests for tests/loadtests/regression_check.py.

The parser sits in a CI gate; a bug here either silently passes a
regression (bad) or fails a clean build (worse). These tests pin the
contract that the gate enforces:

* a measured p95 within ``threshold * GATE_MULTIPLIER`` PASSes,
* one beyond it FAILs,
* any non-zero failure count on a tracked endpoint FAILs,
* an endpoint missing from the CSV is SKIPPED (not silently passed),
* the ``Aggregated`` row never participates in the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.loadtests import regression_check


def _write_stats_csv(path: Path, rows: list[dict]) -> Path:
    """Render a locust-style stats CSV from a list of column dicts."""
    header = (
        'Type,Name,Request Count,Failure Count,Median Response Time,'
        'Average Response Time,Min Response Time,Max Response Time,'
        'Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,'
        '90%,95%,98%,99%,99.9%,99.99%,100%'
    )
    lines = [header]
    for row in rows:
        lines.append(
            ','.join(
                [
                    row.get('Type', 'GET'),
                    row['Name'],
                    str(row.get('Request Count', 100)),
                    str(row.get('Failure Count', 0)),
                    '10',
                    '15',
                    '5',
                    '50',
                    '1000',
                    '1.0',
                    '0.0',
                    '10',
                    '15',
                    '18',
                    '20',
                    '30',
                    str(row['95%']),
                    '60',
                    '70',
                    '80',
                    '90',
                    '100',
                ]
            )
        )
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _write_thresholds(path: Path, mapping: dict[str, float | None]) -> Path:
    path.write_text(
        json.dumps({'thresholds': {name: {'p95_ms': value} for name, value in mapping.items()}}),
        encoding='utf-8',
    )
    return path


def test_load_stats_drops_aggregated_row(tmp_path):
    """The ``Aggregated`` row is bookkeeping, not an endpoint."""
    stats = _write_stats_csv(
        tmp_path / 'stats.csv',
        [
            {'Name': '/', '95%': 50},
            {'Name': 'Aggregated', '95%': 200},
        ],
    )
    measured = regression_check.load_stats(stats)
    assert '/' in measured
    assert 'Aggregated' not in measured


def test_load_thresholds_skips_null_values(tmp_path):
    """Null p95 means 'not yet baselined; skip the gate'."""
    path = _write_thresholds(tmp_path / 't.json', {'/': 70, '/new': None})
    thresholds = regression_check.load_thresholds(path)
    assert thresholds == {'/': 70.0}


def test_compare_pass_within_gate(tmp_path):
    """A measured p95 right at ``threshold * gate`` PASSes (not strict >)."""
    stats = _write_stats_csv(tmp_path / 'stats.csv', [{'Name': '/', '95%': 60}])
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50})  # gate = 50 * 1.2 = 60
    measured = regression_check.load_stats(stats)
    thresholds = regression_check.load_thresholds(thr)
    failures, passes, skipped = regression_check.compare(measured, thresholds)
    assert not failures
    assert len(passes) == 1
    assert passes[0]['endpoint'] == '/'
    assert skipped == []


def test_compare_fail_beyond_gate(tmp_path):
    """One ms over the gate is a FAIL."""
    stats = _write_stats_csv(tmp_path / 'stats.csv', [{'Name': '/', '95%': 61}])
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50})  # gate = 60
    failures, passes, _ = regression_check.compare(
        regression_check.load_stats(stats),
        regression_check.load_thresholds(thr),
    )
    assert len(failures) == 1
    assert not passes
    assert failures[0]['p95_ms'] == 61.0


def test_compare_fail_on_nonzero_failures(tmp_path):
    """A 500 under load is always a regression — independent of latency."""
    stats = _write_stats_csv(
        tmp_path / 'stats.csv',
        [{'Name': '/', '95%': 10, 'Failure Count': 3}],
    )
    thr = _write_thresholds(tmp_path / 't.json', {'/': 100})
    failures, passes, _ = regression_check.compare(
        regression_check.load_stats(stats),
        regression_check.load_thresholds(thr),
    )
    assert len(failures) == 1
    assert not passes
    assert failures[0]['failures'] == 3


def test_compare_skip_when_endpoint_missing_from_csv(tmp_path):
    """An endpoint with a threshold but no measurement is SKIPPED."""
    stats = _write_stats_csv(tmp_path / 'stats.csv', [{'Name': '/', '95%': 30}])
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50, '/missing': 100})
    failures, passes, skipped = regression_check.compare(
        regression_check.load_stats(stats),
        regression_check.load_thresholds(thr),
    )
    assert not failures
    assert len(passes) == 1
    assert skipped == ['/missing']


def test_render_summary_marks_fail_and_pass(tmp_path):
    """The rendered table carries both verdicts when both occur."""
    stats = _write_stats_csv(
        tmp_path / 'stats.csv',
        [
            {'Name': '/', '95%': 30},  # pass: 30 <= 50 * 1.2
            {'Name': '/portfolio', '95%': 100},  # fail: 100 > 50 * 1.2
        ],
    )
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50, '/portfolio': 50})
    failures, passes, skipped = regression_check.compare(
        regression_check.load_stats(stats),
        regression_check.load_thresholds(thr),
    )
    summary = regression_check.render_summary(failures, passes, skipped)
    assert '| PASS |' in summary
    assert '| FAIL |' in summary
    assert '/portfolio' in summary


def test_main_returns_1_on_regression(tmp_path):
    """End-to-end: ``main()`` exits 1 when any endpoint regresses."""
    stats = _write_stats_csv(tmp_path / 'stats.csv', [{'Name': '/', '95%': 200}])
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50})
    rc = regression_check.main(['--stats', str(stats), '--thresholds', str(thr)])
    assert rc == 1


def test_main_returns_0_on_clean_run(tmp_path):
    """End-to-end: ``main()`` exits 0 when every tracked endpoint PASSes."""
    stats = _write_stats_csv(tmp_path / 'stats.csv', [{'Name': '/', '95%': 20}])
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50})
    rc = regression_check.main(['--stats', str(stats), '--thresholds', str(thr)])
    assert rc == 0


def test_main_returns_2_on_missing_stats(tmp_path):
    """Missing CSV is an operator error, not a regression — distinct exit."""
    thr = _write_thresholds(tmp_path / 't.json', {'/': 50})
    rc = regression_check.main(['--stats', str(tmp_path / 'nope.csv'), '--thresholds', str(thr)])
    assert rc == 2
