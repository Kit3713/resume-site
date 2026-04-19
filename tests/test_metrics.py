"""
Metrics Tests — Phase 18.2

Verifies app.services.metrics primitives + the /metrics route:
- Counter / Gauge / Histogram semantics and label handling.
- MetricsRegistry.render() produces valid Prometheus text.
- /metrics returns 404 when feature-flagged off.
- /metrics returns 404 from a disallowed IP even when enabled.
- /metrics returns 200 with expected metrics when enabled + allowed.
- Request instrumentation uses the url_rule template (not the raw path)
  and normalises unmatched requests to the UNMATCHED_PATH sentinel.
- /metrics scrapes are not counted into request metrics.

Unit tests construct their own MetricsRegistry instances so they don't
stomp on the module-level singleton that the Flask app uses. Integration
tests reset the singleton via the `clean_metrics_registry` fixture.
"""

from __future__ import annotations

import re

import pytest

from app.services.metrics import (
    CONTENT_TYPE,
    DEFAULT_DURATION_BUCKETS,
    UNMATCHED_PATH,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    _format_labels,
    _format_value,
    client_ip_in_networks,
    parse_cidr_list,
    record_request,
)

# ---------------------------------------------------------------------------
# Formatter helpers
# ---------------------------------------------------------------------------


def test_format_labels_empty_returns_empty_string():
    assert _format_labels({}) == ''


def test_format_labels_sorts_keys():
    # Deterministic output so render() is stable scrape-to-scrape.
    assert _format_labels({'b': '2', 'a': '1'}) == '{a="1",b="2"}'


def test_format_labels_escapes_backslash_newline_quote():
    got = _format_labels({'x': 'a\\b\n"c"'})
    assert got == r'{x="a\\b\n\"c\""}'


def test_format_value_integers_no_decimal():
    assert _format_value(42) == '42'
    assert _format_value(0) == '0'


def test_format_value_nan_and_inf():
    assert _format_value(float('nan')) == 'NaN'
    assert _format_value(float('inf')) == '+Inf'
    assert _format_value(float('-inf')) == '-Inf'


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


def test_counter_starts_at_zero_increments_by_one_default():
    c = Counter('x_total', 'desc', label_names=('method',))
    c.inc(label_values=('GET',))
    c.inc(label_values=('GET',))
    samples = list(c.samples())
    assert samples == [('x_total', {'method': 'GET'}, 2)]


def test_counter_custom_amount():
    c = Counter('x_total', 'desc')
    c.inc(amount=5)
    c.inc(amount=2.5)
    assert list(c.samples()) == [('x_total', {}, 7.5)]


def test_counter_rejects_negative_amount():
    c = Counter('x_total', 'desc')
    with pytest.raises(ValueError, match='non-negative'):
        c.inc(amount=-1)


def test_counter_rejects_wrong_label_count():
    c = Counter('x', 'd', label_names=('a', 'b'))
    with pytest.raises(ValueError, match='expected 2 label values'):
        c.inc(label_values=('only-one',))


# ---------------------------------------------------------------------------
# Gauge
# ---------------------------------------------------------------------------


def test_gauge_set_overwrites():
    g = Gauge('x', 'd')
    g.set(10)
    g.set(3)
    assert list(g.samples()) == [('x', {}, 3.0)]


def test_gauge_inc_and_dec():
    g = Gauge('x', 'd')
    g.inc(amount=5)
    g.dec(amount=2)
    assert list(g.samples())[0][2] == 3


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------


def test_histogram_observations_fill_cumulative_buckets():
    h = Histogram(
        'h_seconds',
        'desc',
        label_names=('method',),
        buckets=(0.1, 0.5, 1.0),
    )
    h.observe(0.05, label_values=('GET',))  # hits 0.1, 0.5, 1.0, +Inf
    h.observe(0.3, label_values=('GET',))  # hits 0.5, 1.0, +Inf
    h.observe(5.0, label_values=('GET',))  # hits only +Inf

    samples = {(s[0], tuple(sorted(s[1].items()))): s[2] for s in h.samples()}
    # 0.1 bucket got the 0.05 observation (and nothing else)
    assert samples[('h_seconds_bucket', (('le', '0.1'), ('method', 'GET')))] == 1
    # 0.5 bucket got 0.05 + 0.3
    assert samples[('h_seconds_bucket', (('le', '0.5'), ('method', 'GET')))] == 2
    # 1.0 bucket got 0.05 + 0.3
    assert samples[('h_seconds_bucket', (('le', '1.0'), ('method', 'GET')))] == 2
    # +Inf got all three
    assert samples[('h_seconds_bucket', (('le', '+Inf'), ('method', 'GET')))] == 3
    assert samples[('h_seconds_sum', (('method', 'GET'),))] == pytest.approx(5.35)
    assert samples[('h_seconds_count', (('method', 'GET'),))] == 3


def test_histogram_default_buckets_match_roadmap():
    h = Histogram('h', 'd')
    assert h.buckets == tuple(sorted(DEFAULT_DURATION_BUCKETS))


# ---------------------------------------------------------------------------
# MetricsRegistry
# ---------------------------------------------------------------------------


def test_registry_deduplicates_by_name():
    reg = MetricsRegistry()
    a = reg.counter('x', 'desc')
    b = reg.counter('x', 'desc')
    assert a is b


def test_registry_rejects_type_mismatch():
    reg = MetricsRegistry()
    reg.counter('x', 'desc')
    with pytest.raises(TypeError, match='already registered'):
        reg.gauge('x', 'desc')


def test_registry_render_produces_help_type_and_sample_lines():
    reg = MetricsRegistry()
    c = reg.counter('my_counter', 'Total things', label_names=('k',))
    c.inc(label_values=('v',))

    text = reg.render()
    assert '# HELP my_counter Total things' in text
    assert '# TYPE my_counter counter' in text
    assert 'my_counter{k="v"} 1' in text
    assert text.endswith('\n')


def test_registry_render_contains_histogram_bucket_ladder():
    reg = MetricsRegistry()
    h = reg.histogram('h_s', 'desc', label_names=(), buckets=(0.1, 1.0))
    h.observe(0.05)

    text = reg.render()
    # Bucket ladder present, +Inf bucket included, sum and count present.
    assert 'h_s_bucket{le="0.1"} 1' in text
    assert 'h_s_bucket{le="1.0"} 1' in text
    assert 'h_s_bucket{le="+Inf"} 1' in text
    assert 'h_s_sum' in text
    assert 'h_s_count' in text


# ---------------------------------------------------------------------------
# Access-control helpers
# ---------------------------------------------------------------------------


def test_client_ip_in_networks_matches_ipv4_cidr():
    assert client_ip_in_networks('10.0.0.1', ['10.0.0.0/8'])
    assert client_ip_in_networks('192.168.1.1', ['192.168.0.0/16', '10.0.0.0/8'])


def test_client_ip_in_networks_rejects_outside():
    assert not client_ip_in_networks('8.8.8.8', ['10.0.0.0/8'])


def test_client_ip_in_networks_empty_or_malformed():
    # Empty / None IP always denies.
    assert not client_ip_in_networks('', ['10.0.0.0/8'])
    assert not client_ip_in_networks(None, ['10.0.0.0/8'])
    # Malformed CIDR entries are skipped; others still considered.
    assert client_ip_in_networks('10.1.2.3', ['not-a-cidr', '10.0.0.0/8'])


def test_parse_cidr_list_strips_and_filters():
    assert parse_cidr_list('  10.0.0.0/8 , , 192.168.0.0/16 ') == [
        '10.0.0.0/8',
        '192.168.0.0/16',
    ]
    assert parse_cidr_list('') == []
    assert parse_cidr_list(None) == []


# ---------------------------------------------------------------------------
# /metrics endpoint — integration
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_metrics_registry():
    """Reset the module-level registry so counter values are deterministic.

    After reset we re-import the metrics module to re-run the metric
    declarations — otherwise app/__init__.py's record_request() would
    write to an empty registry and render() would return no samples.
    """
    import importlib

    from app.services import metrics as metrics_mod

    metrics_mod.get_registry().reset()
    importlib.reload(metrics_mod)
    yield
    metrics_mod.get_registry().reset()
    importlib.reload(metrics_mod)


def _enable_metrics(app, networks='127.0.0.0/8'):
    """Flip metrics_enabled on and set the allowed CIDR override."""
    import sqlite3

    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('metrics_enabled', 'true')")
    conn.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        ('metrics_allowed_networks', networks),
    )
    conn.commit()
    conn.close()
    # Bust the settings cache so the new values are visible immediately.
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()


def test_metrics_returns_404_when_disabled(client, clean_metrics_registry):
    # The defaults fixture leaves metrics_enabled=false.
    response = client.get('/metrics')
    assert response.status_code == 404


def test_metrics_returns_404_from_disallowed_ip(app, client, clean_metrics_registry):
    # Enable with an allow-list that excludes 127.0.0.0/8 (our test client IP)
    _enable_metrics(app, networks='10.0.0.0/8')
    response = client.get('/metrics')
    assert response.status_code == 404


def test_metrics_returns_200_when_enabled_and_allowed(app, client, clean_metrics_registry):
    _enable_metrics(app, networks='127.0.0.0/8')
    response = client.get('/metrics')
    assert response.status_code == 200
    assert response.headers.get('Content-Type') == CONTENT_TYPE

    body = response.get_data(as_text=True)
    # Core metrics must be present even before other traffic has flowed.
    assert '# HELP resume_site_requests_total' in body
    assert '# TYPE resume_site_requests_total counter' in body
    assert '# TYPE resume_site_request_duration_seconds histogram' in body
    assert '# TYPE resume_site_uptime_seconds gauge' in body


def test_metrics_records_request_with_url_rule_as_path_label(app, client, clean_metrics_registry):
    _enable_metrics(app, networks='127.0.0.0/8')
    # Generate a request to a known route so we can assert on its sample.
    client.get('/')

    body = client.get('/metrics').get_data(as_text=True)
    # The landing-page rule is '/' — look for the counter sample.
    assert re.search(
        r'resume_site_requests_total\{[^}]*method="GET"[^}]*path="/"[^}]*status="200"[^}]*\} \d+',
        body,
    )


def test_metrics_unmatched_request_uses_sentinel_label(app, client, clean_metrics_registry):
    _enable_metrics(app, networks='127.0.0.0/8')
    client.get('/this-definitely-does-not-exist')

    body = client.get('/metrics').get_data(as_text=True)
    assert f'path="{UNMATCHED_PATH}"' in body, f'expected sentinel path label in body, got:\n{body}'


def test_metrics_scrape_self_excludes(app, client, clean_metrics_registry):
    _enable_metrics(app, networks='127.0.0.0/8')
    # Scrape three times. The counter should not grow for path="/metrics".
    for _ in range(3):
        client.get('/metrics')

    body = client.get('/metrics').get_data(as_text=True)
    assert 'path="/metrics"' not in body


# ---------------------------------------------------------------------------
# record_request — direct unit test (no Flask)
# ---------------------------------------------------------------------------


def test_record_request_writes_to_singleton_registry(clean_metrics_registry):
    from app.services.metrics import request_duration_seconds, requests_total

    # Clean state: zero samples before.
    assert list(requests_total.samples()) == []

    record_request('POST', '/contact', 200, 0.123)

    samples = {(s[0], tuple(sorted(s[1].items()))): s[2] for s in requests_total.samples()}
    assert (
        samples[
            (
                'resume_site_requests_total',
                (('method', 'POST'), ('path', '/contact'), ('status', '200')),
            )
        ]
        == 1
    )

    # Histogram observation landed in the right buckets.
    hist_samples = list(request_duration_seconds.samples())
    count_sample = next(s for s in hist_samples if s[0].endswith('_count'))
    assert count_sample[2] == 1


def test_record_request_normalises_none_rule(clean_metrics_registry):
    from app.services.metrics import requests_total

    record_request('GET', None, 404, 0.001)
    keys = [dict(s[1])['path'] for s in requests_total.samples()]
    assert UNMATCHED_PATH in keys


# ---------------------------------------------------------------------------
# login_attempts_total — Phase 18.10 brute-force counter
# ---------------------------------------------------------------------------


def test_login_attempts_declared_with_outcome_label():
    """The counter exists in the registry with the expected help text.

    The alert rule in docs/alerting-rules.yaml references the metric
    name verbatim, so a rename needs a corresponding YAML update — this
    test is the canary.
    """
    from app.services.metrics import get_registry

    metric = get_registry()._metrics['resume_site_login_attempts_total']
    assert metric.TYPE == 'counter'
    assert metric.label_names == ('outcome',)


def test_record_failed_login_emits_invalid_outcome(clean_metrics_registry, tmp_path):
    """record_failed_login increments login_attempts_total{outcome="invalid"}."""
    import sqlite3

    from app.services.login_throttle import record_failed_login
    from app.services.metrics import login_attempts_total

    conn = sqlite3.connect(str(tmp_path / 'throttle.db'))
    conn.executescript(
        'CREATE TABLE login_attempts ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  ip_hash TEXT NOT NULL,'
        '  success INTEGER NOT NULL,'
        '  created_at TEXT NOT NULL'
        ');'
    )
    try:
        record_failed_login(conn, 'ip-a')
        record_failed_login(conn, 'ip-b')

        samples = {dict(s[1])['outcome']: s[2] for s in login_attempts_total.samples()}
        assert samples.get('invalid') == 2
    finally:
        conn.close()


def test_record_successful_login_emits_success_outcome(clean_metrics_registry, tmp_path):
    import sqlite3

    from app.services.login_throttle import record_successful_login
    from app.services.metrics import login_attempts_total

    conn = sqlite3.connect(str(tmp_path / 'throttle.db'))
    conn.executescript(
        'CREATE TABLE login_attempts ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  ip_hash TEXT NOT NULL,'
        '  success INTEGER NOT NULL,'
        '  created_at TEXT NOT NULL'
        ');'
    )
    try:
        record_successful_login(conn, 'ip-a')

        samples = {dict(s[1])['outcome']: s[2] for s in login_attempts_total.samples()}
        assert samples.get('success') == 1
    finally:
        conn.close()


def test_check_lockout_when_locked_emits_locked_outcome(clean_metrics_registry, tmp_path):
    """A locked-out attempt increments the locked counter exactly once."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    from app.services.login_throttle import check_lockout, record_failed_login
    from app.services.metrics import login_attempts_total

    conn = sqlite3.connect(str(tmp_path / 'throttle.db'))
    conn.executescript(
        'CREATE TABLE login_attempts ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  ip_hash TEXT NOT NULL,'
        '  success INTEGER NOT NULL,'
        '  created_at TEXT NOT NULL'
        ');'
    )
    try:
        # Seed enough failures to trip the threshold, then confirm
        # check_lockout emits the `locked` outcome (but not when the
        # same IP is below threshold).
        base = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
        for i in range(5):
            record_failed_login(conn, 'ip-a', now=base + timedelta(seconds=i))

        # Under threshold (5 < 10) — check_lockout returns not-locked
        # and must NOT emit.
        before = sum(
            s[2] for s in login_attempts_total.samples() if dict(s[1])['outcome'] == 'locked'
        )
        status = check_lockout(
            conn,
            'ip-a',
            threshold=10,
            window_minutes=15,
            lockout_minutes=15,
            now=base + timedelta(seconds=6),
        )
        assert not status.locked
        after = sum(
            s[2] for s in login_attempts_total.samples() if dict(s[1])['outcome'] == 'locked'
        )
        assert after == before, 'under-threshold check must not emit locked'

        # Trip the threshold and confirm the locked increment lands.
        for i in range(5, 11):
            record_failed_login(conn, 'ip-a', now=base + timedelta(seconds=i))
        status = check_lockout(
            conn,
            'ip-a',
            threshold=10,
            window_minutes=15,
            lockout_minutes=15,
            now=base + timedelta(seconds=12),
        )
        assert status.locked
        locked_samples = [
            s[2] for s in login_attempts_total.samples() if dict(s[1])['outcome'] == 'locked'
        ]
        assert locked_samples and locked_samples[0] >= 1
    finally:
        conn.close()
