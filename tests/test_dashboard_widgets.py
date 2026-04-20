"""
Tests for the v0.3.1 dashboard additions (Phases 36.3 and 36.5).

- Translation completeness matrix: the coverage helper and the rendered table.
- In-app alerting widget: YAML parsing + category extraction + rule firing.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.services.alerting import clear_cache, get_active_alerts
from app.services.translations import get_coverage_matrix

# ---------------------------------------------------------------------------
# 36.3 — translation coverage matrix
# ---------------------------------------------------------------------------


def _row(db: sqlite3.Connection, sql: str, params=()) -> None:
    db.execute(sql, params)
    db.commit()


def test_coverage_matrix_default_locale_is_full(populated_db):
    """The default locale always reports the parent-table row count."""
    matrix = get_coverage_matrix(populated_db, ['en'], default_locale='en')

    by_type = {row['type']: row for row in matrix}
    assert 'services' in by_type
    # All content types should list the default locale at full coverage.
    for row in matrix:
        assert row['coverage']['en'] == row['total']


def test_coverage_matrix_missing_translations_are_zero(populated_db):
    """Locales without any translation rows report zero."""
    matrix = get_coverage_matrix(populated_db, ['en', 'es', 'fr'], default_locale='en')
    for row in matrix:
        assert row['coverage']['es'] == 0
        assert row['coverage']['fr'] == 0


def test_coverage_matrix_counts_translation_rows(populated_db):
    """Each translation row adds one to the per-locale coverage count."""
    # Add a single Spanish translation for the seeded service row.
    service_id = populated_db.execute('SELECT id FROM services LIMIT 1').fetchone()['id']
    _row(
        populated_db,
        'INSERT INTO service_translations (service_id, locale, title, description) '
        'VALUES (?, ?, ?, ?)',
        (service_id, 'es', 'Desarrollo web', 'Aplicaciones full-stack'),
    )

    matrix = get_coverage_matrix(populated_db, ['en', 'es'], default_locale='en')
    services = next(row for row in matrix if row['type'] == 'services')
    assert services['coverage']['es'] == 1
    assert services['coverage']['en'] == services['total']


def test_coverage_matrix_empty_parent_tables(app):
    """Empty parent tables yield zero coverage for every non-default locale."""
    db_path = app.config['DATABASE_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        matrix = get_coverage_matrix(conn, ['en', 'es'], default_locale='en')
    finally:
        conn.close()

    for row in matrix:
        assert row['coverage']['en'] == row['total']
        assert row['coverage']['es'] == 0


def test_dashboard_renders_translation_matrix(auth_client, populated_db):
    """The dashboard includes the matrix section when multiple locales are configured."""
    _row(
        populated_db,
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('available_locales', 'en,es')",
    )
    _row(
        populated_db,
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('default_locale', 'en')",
    )

    # Bust the settings cache so the inserts above are visible.
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    resp = auth_client.get('/admin/')
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'Translation Completeness' in body
    assert 'services' in body


def test_dashboard_skips_matrix_for_single_locale(auth_client, populated_db):
    """A deployment with only one configured locale hides the matrix entirely."""
    _row(
        populated_db,
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('available_locales', 'en')",
    )
    from app.services.settings_svc import invalidate_cache

    invalidate_cache()

    resp = auth_client.get('/admin/')
    assert resp.status_code == 200
    assert 'Translation Completeness' not in resp.data.decode()


# ---------------------------------------------------------------------------
# 36.5 — in-app alerting widget
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_alerting_cache():
    clear_cache()
    yield
    clear_cache()


def test_alerting_parses_category_rules():
    """Rules with resume_site_errors_total{category="X"} load into the cache."""
    alerts = get_active_alerts({'InternalError': 3})
    names = [a['alert'] for a in alerts]
    assert 'ResumeInternalErrorRate' in names


def test_alerting_skips_non_category_rules():
    """Rules keyed on latency / uptime / disk usage are not surfaced."""
    alerts = get_active_alerts({'InternalError': 1})
    # These exist in alerting-rules.yaml but reference non-errors_total metrics.
    names = {a['alert'] for a in alerts}
    assert 'ResumeHighLatency' not in names
    assert 'ResumeProcessRestarted' not in names
    assert 'ResumeScrapeDown' not in names


def test_alerting_inactive_when_counter_is_zero():
    """No counter hits → no alerts surface, even for rules that exist."""
    alerts = get_active_alerts({})
    assert alerts == []

    alerts = get_active_alerts({'InternalError': 0})
    assert alerts == []


def test_alerting_sorts_by_severity():
    """Critical alerts sort before warning, warning before info."""
    alerts = get_active_alerts({'InternalError': 1, 'AuthError': 1})
    severities = [a['severity'] for a in alerts]
    assert severities == sorted(severities, key={'critical': 0, 'warning': 1, 'info': 2}.get)


def test_dashboard_renders_alerts_card(auth_client):
    """Active Alerts card is always rendered — value shows zero when idle."""
    resp = auth_client.get('/admin/')
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'Active Alerts' in body
