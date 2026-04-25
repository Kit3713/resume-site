"""
Alerting Rules Tests — Phase 18.10

Validates docs/alerting-rules.yaml:

* Parses as valid YAML with the Prometheus ``groups``/``rules`` schema.
* Every rule has the required fields (alert, expr, for, labels.severity,
  annotations.summary, annotations.description, annotations.runbook_url).
* Every custom metric name referenced in a rule's ``expr`` exists in
  :mod:`app.services.metrics` — catches "I renamed a metric but forgot
  to update the alert expression" bugs at CI time rather than at incident
  time.
* The histogram alert references the ``_bucket`` suffix (mandatory for
  ``histogram_quantile``).
* Every alert's ``runbook_url`` points at an anchor that actually exists
  in ``docs/alerting-rules.md``.

These checks are cheap and catch real drift.
"""

from __future__ import annotations

import os
import re

import pytest

yaml = pytest.importorskip('yaml')  # stdlib doesn't include it — available via PyYAML


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
RULES_YAML = os.path.join(PROJECT_ROOT, 'docs', 'alerting-rules.yaml')
RUNBOOK_MD = os.path.join(PROJECT_ROOT, 'docs', 'alerting-rules.md')
GRAFANA_JSON = os.path.join(PROJECT_ROOT, 'docs', 'grafana-dashboard.json')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def rules_document():
    with open(RULES_YAML) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope='module')
def all_rules(rules_document):
    out = []
    for group in rules_document['groups']:
        for rule in group['rules']:
            rule['_group'] = group['name']
            out.append(rule)
    return out


@pytest.fixture(scope='module')
def custom_metric_names():
    """Set of metric names actually declared in the registry.

    The module-level declarations in app.services.metrics are the source
    of truth. We read them once at test module load and compare against
    every ``resume_site_*`` name we find in rule expressions.
    """
    from app.services.metrics import get_registry

    return {metric.name for metric in get_registry()._metrics.values()}


@pytest.fixture(scope='module')
def runbook_headings():
    """Return the set of lowercased H2 headings in alerting-rules.md."""
    with open(RUNBOOK_MD) as f:
        text = f.read()
    # Markdown H2: `## Heading`
    return {m.group(1).strip().lower() for m in re.finditer(r'^##\s+([^\n]+)$', text, re.MULTILINE)}


# ---------------------------------------------------------------------------
# Structural YAML
# ---------------------------------------------------------------------------


def test_rules_file_exists():
    assert os.path.isfile(RULES_YAML), f'missing: {RULES_YAML}'


def test_rules_document_has_groups_key(rules_document):
    assert 'groups' in rules_document
    assert isinstance(rules_document['groups'], list)
    assert rules_document['groups'], 'at least one group expected'


def test_every_group_has_name_and_rules(rules_document):
    for group in rules_document['groups']:
        assert 'name' in group, f'group missing name: {group}'
        assert group['name'].startswith('resume-site-'), (
            f'group name should be prefixed resume-site-: {group["name"]!r}'
        )
        assert 'rules' in group and group['rules'], f'group {group["name"]!r} has no rules'


def test_at_least_one_rule_per_severity(all_rules):
    severities = {rule['labels']['severity'] for rule in all_rules}
    # We explicitly ship at least one critical + one warning. info is optional
    # but nice to have.
    assert 'critical' in severities
    assert 'warning' in severities


# ---------------------------------------------------------------------------
# Per-rule required fields
# ---------------------------------------------------------------------------


REQUIRED_TOP_KEYS = {'alert', 'expr', 'for', 'labels', 'annotations'}
REQUIRED_LABEL_KEYS = {'severity', 'component'}
REQUIRED_ANNOTATION_KEYS = {'summary', 'description', 'runbook_url'}
ALLOWED_SEVERITIES = {'critical', 'warning', 'info'}
ALLOWED_COMPONENTS = {
    'application',
    'security',
    'performance',
    'traffic',
    'availability',
    'backup',
    'storage',
}


@pytest.mark.parametrize('rule_key', sorted(REQUIRED_TOP_KEYS))
def test_every_rule_has_required_top_level_keys(all_rules, rule_key):
    for rule in all_rules:
        assert rule_key in rule, (
            f'rule {rule.get("alert", "<unnamed>")!r} in group '
            f'{rule["_group"]!r} is missing top-level key {rule_key!r}'
        )


def test_every_rule_has_required_labels(all_rules):
    for rule in all_rules:
        labels = rule['labels']
        missing = REQUIRED_LABEL_KEYS - labels.keys()
        assert not missing, f'{rule["alert"]!r} labels missing: {missing}'
        assert labels['severity'] in ALLOWED_SEVERITIES, (
            f'{rule["alert"]!r} has unknown severity {labels["severity"]!r}'
        )
        assert labels['component'] in ALLOWED_COMPONENTS, (
            f'{rule["alert"]!r} has unknown component {labels["component"]!r}'
        )


def test_every_rule_has_required_annotations(all_rules):
    for rule in all_rules:
        annos = rule['annotations']
        missing = REQUIRED_ANNOTATION_KEYS - annos.keys()
        assert not missing, f'{rule["alert"]!r} annotations missing: {missing}'
        for key, value in annos.items():
            assert isinstance(value, str) and value.strip(), (
                f'{rule["alert"]!r} annotation {key!r} must be a non-empty string'
            )


def test_alert_names_are_camelcase_and_unique(all_rules):
    names = [rule['alert'] for rule in all_rules]
    assert len(names) == len(set(names)), f'duplicate alert names: {names}'
    for name in names:
        assert re.match(r'^[A-Z][A-Za-z0-9]+$', name), (
            f'alert name {name!r} should be CamelCase without punctuation'
        )


def test_for_durations_parse(all_rules):
    duration_re = re.compile(r'^\d+(ms|s|m|h|d|w)$')
    for rule in all_rules:
        assert duration_re.match(str(rule['for'])), (
            f'{rule["alert"]!r} has unparseable "for": {rule["for"]!r}'
        )


# ---------------------------------------------------------------------------
# Metric-name consistency — catches rename drift
# ---------------------------------------------------------------------------


# Match resume_site_*  plus the optional Prometheus-synthesised suffix
# (_bucket / _sum / _count for histograms).
METRIC_RE = re.compile(r'\bresume_site_[A-Za-z0-9_]+\b')

# Strip histogram suffixes so we compare against the registry name.
HISTOGRAM_SUFFIXES = ('_bucket', '_sum', '_count')


def _base_metric_name(name):
    for suffix in HISTOGRAM_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def test_every_referenced_metric_exists(all_rules, custom_metric_names):
    for rule in all_rules:
        referenced = set(METRIC_RE.findall(rule['expr']))
        for name in referenced:
            base = _base_metric_name(name)
            assert base in custom_metric_names, (
                f'{rule["alert"]!r} references metric {name!r} '
                f'(base {base!r}) which is not registered in '
                f'app.services.metrics. Registered: '
                f'{sorted(custom_metric_names)}'
            )


def test_latency_rule_uses_bucket_suffix(all_rules):
    """histogram_quantile needs _bucket, not the bare histogram name."""
    latency = next(r for r in all_rules if r['alert'] == 'ResumeHighLatency')
    assert 'resume_site_request_duration_seconds_bucket' in latency['expr'], (
        'ResumeHighLatency must use the _bucket series for histogram_quantile'
    )


def test_every_custom_metric_is_referenced_at_least_once(all_rules, custom_metric_names):
    """Surface drift in the OTHER direction: shipped a metric but never
    alerted on it.

    Not every metric needs an alert — but if we haven't thought about
    one, this test surfaces the gap during review. Update the known
    exemption list as alerts are intentionally omitted.
    """
    # Uptime is surfaced through ResumeProcessRestarted; errors are in
    # ResumeInternalErrorRate / ResumeAuthErrorSpike; request counters in
    # ResumeHighRequestRate / ResumeNoTraffic; duration in ResumeHighLatency.
    # This test is a canary — if a new metric lands without an alert
    # intentionally, exempt it here with a comment.
    EXEMPT: set[str] = {
        # Informational counters — useful for dashboards but no natural
        # alerting threshold. Operators who want alerts on these can add
        # custom rules referencing the metric names directly.
        'resume_site_photo_uploads_total',
        'resume_site_contact_submissions_total',
        'resume_site_blog_posts_total',
        # Phase 37.2: deprecated-API call counter exists so operators can
        # see consumer migration progress as a sunset date approaches.
        # Not alertable on its own — a non-zero value is informational
        # (operators set their own threshold per deprecation timeline).
        'resume_site_deprecated_api_calls_total',
    }

    all_expr_text = '\n'.join(r['expr'] for r in all_rules)
    referenced = set(METRIC_RE.findall(all_expr_text))
    referenced_bases = {_base_metric_name(n) for n in referenced}

    unreferenced = custom_metric_names - referenced_bases - EXEMPT
    assert not unreferenced, (
        f'metrics declared but not alerted on: {sorted(unreferenced)}. '
        'Add a rule OR add to EXEMPT with a comment explaining why.'
    )


# ---------------------------------------------------------------------------
# Runbook coverage
# ---------------------------------------------------------------------------


def test_every_runbook_url_points_at_an_existing_heading(all_rules, runbook_headings):
    for rule in all_rules:
        url = rule['annotations']['runbook_url']
        # Expect ./alerting-rules.md#Anchor form. Extract the anchor.
        match = re.search(r'#([A-Za-z0-9_-]+)', url)
        assert match, f'{rule["alert"]!r} runbook_url lacks a #anchor: {url!r}'
        anchor = match.group(1).lower()
        # GitHub / most markdown renderers lowercase anchors.
        # Our headings are alert names directly. Compare case-insensitively.
        assert anchor in runbook_headings, (
            f'{rule["alert"]!r} runbook_url {url!r} references heading '
            f'{anchor!r} which does not exist in docs/alerting-rules.md. '
            f'Existing headings: {sorted(runbook_headings)}'
        )


def test_runbook_has_severity_and_setup_sections(runbook_headings):
    # Presence of the general sections operators need on first open.
    assert any(h in runbook_headings for h in ('setup',))
    assert any(h in runbook_headings for h in ('severity taxonomy',))


# ---------------------------------------------------------------------------
# Grafana dashboard (Phase 18.11) — drift guard
# ---------------------------------------------------------------------------
#
# The dashboard references resume_site_* metrics by name in panel targets.
# If someone renames or removes a metric in app/services/metrics.py, the
# dashboard needs a matching update. These tests catch that drift at CI
# time rather than at import time in Grafana.


@pytest.fixture(scope='module')
def dashboard_document():
    import json

    with open(GRAFANA_JSON) as f:
        return json.load(f)


@pytest.fixture(scope='module')
def dashboard_panels(dashboard_document):
    return dashboard_document['panels']


def _panel_metric_expressions(panels):
    """Yield every ``targets[].expr`` string from every panel.

    Handles nested row panels (Grafana's row container) by walking
    ``panels`` keys recursively.
    """
    for panel in panels:
        for target in panel.get('targets', []) or []:
            expr = target.get('expr')
            if expr:
                yield panel.get('title', '<unnamed>'), expr
        # Recurse into nested panels (row containers) if present.
        nested = panel.get('panels')
        if nested:
            yield from _panel_metric_expressions(nested)


def test_dashboard_file_is_valid_json(dashboard_document):
    """Parses cleanly. Top-level required keys are present."""
    for key in ('title', 'uid', 'panels', 'schemaVersion', 'templating'):
        assert key in dashboard_document, f'dashboard missing top-level key: {key!r}'
    assert dashboard_document['panels'], 'dashboard has no panels'


def test_dashboard_exposes_prometheus_datasource_variable(dashboard_document):
    """The dashboard must offer a $DS_PROMETHEUS variable so operators pick their DS on import."""
    variables = dashboard_document['templating']['list']
    names = {v['name'] for v in variables}
    assert 'DS_PROMETHEUS' in names, f'expected DS_PROMETHEUS variable, got {sorted(names)}'
    ds = next(v for v in variables if v['name'] == 'DS_PROMETHEUS')
    assert ds['type'] == 'datasource'
    assert ds['query'] == 'prometheus'


def test_dashboard_declares_prometheus_input(dashboard_document):
    """__inputs declaration is required for Grafana's import-from-JSON UI."""
    inputs = dashboard_document.get('__inputs', [])
    assert inputs, '__inputs missing — Grafana import UI will not offer a datasource picker'
    prom = next((i for i in inputs if i.get('pluginId') == 'prometheus'), None)
    assert prom is not None, 'no Prometheus input declared'
    assert prom['name'] == 'DS_PROMETHEUS'


def test_every_panel_references_datasource_variable(dashboard_panels):
    """Each target must point at ${DS_PROMETHEUS} — not a hardcoded uid."""
    for panel in dashboard_panels:
        for target in panel.get('targets', []) or []:
            ds = target.get('datasource', {})
            if isinstance(ds, dict):
                assert ds.get('uid') == '${DS_PROMETHEUS}', (
                    f'panel {panel.get("title")!r} target has non-variable '
                    f'datasource uid {ds.get("uid")!r}'
                )


def test_every_dashboard_metric_exists_in_registry(dashboard_panels, custom_metric_names):
    """Every resume_site_* name in a panel query must be a registered metric.

    Catches "renamed a metric without updating the dashboard JSON" bugs at
    CI time. The same base-name normalisation used for alerting rules
    applies: histogram suffixes (_bucket, _sum, _count) strip back to
    the declared metric name.
    """
    missing = []
    for title, expr in _panel_metric_expressions(dashboard_panels):
        for name in set(METRIC_RE.findall(expr)):
            base = _base_metric_name(name)
            if base not in custom_metric_names:
                missing.append((title, name, base))
    assert not missing, (
        'dashboard references metrics not registered in app.services.metrics:\n'
        + '\n'.join(f'  panel={t!r} referenced={n!r} base={b!r}' for (t, n, b) in missing)
        + f'\nregistered: {sorted(custom_metric_names)}'
    )


def test_dashboard_every_panel_has_title_and_grid_pos(dashboard_panels):
    """Structural sanity: each panel must carry a title and a gridPos."""
    for i, panel in enumerate(dashboard_panels):
        assert panel.get('title'), f'panel index {i} has no title'
        grid = panel.get('gridPos', {})
        for key in ('x', 'y', 'w', 'h'):
            assert key in grid, f'panel {panel["title"]!r} gridPos missing {key!r}'


def test_dashboard_panel_ids_are_unique(dashboard_panels):
    """Grafana uses panel id for permalinking; duplicates break panel URLs."""
    ids = [p['id'] for p in dashboard_panels if 'id' in p]
    assert len(ids) == len(set(ids)), f'duplicate panel ids: {ids}'
