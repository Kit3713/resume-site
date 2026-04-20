"""
In-app alerting widget (Phase 36.5).

Surfaces the subset of ``docs/alerting-rules.yaml`` rules that can be
evaluated locally against the in-memory ``resume_site_errors_total``
counter, without a live Prometheus scrape. The full Prometheus +
Alertmanager pipeline remains authoritative for production alerting;
this widget is a same-pane-of-glass preview for the admin dashboard.

Scope:
- Rules whose expression targets ``resume_site_errors_total{category="X"}``
  are extracted at startup and evaluated against the category-count snapshot
  the dashboard already collects.
- Rules whose expression targets latency histograms, uptime, disk usage,
  or the backup gauge cannot be evaluated from a counter snapshot and are
  deliberately skipped — Prometheus is the right tool for those.

The YAML is parsed once at startup and cached. PyYAML is already in
``requirements.txt``; no new runtime dependency.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).resolve().parent.parent.parent / 'docs' / 'alerting-rules.yaml'

_cache_lock = threading.Lock()
_cached_rules: list[dict] | None = None

# Matches the category label in a resume_site_errors_total{category="X"} clause.
_CATEGORY_PATTERN = re.compile(r'resume_site_errors_total\{[^}]*category="([A-Za-z_]+)"')


def _load_rules_yaml(path: Path) -> list[dict]:
    """Parse the alerting rules YAML and keep only the in-process-evaluable subset.

    Each returned dict carries ``alert``, ``severity``, ``summary``,
    ``category`` (the error category the rule watches), and the raw
    ``expr`` for display. Unparseable files and unknown schemas fall back
    to an empty list — the widget shows "no active alerts" instead of
    breaking the dashboard.
    """
    try:
        import yaml

        with open(path, encoding='utf-8') as fh:
            parsed = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.debug('alerting-rules.yaml not found at %s', path)
        return []
    except Exception as exc:  # noqa: BLE001 — diagnostic, never break boot
        logger.warning('failed to parse alerting-rules.yaml: %s', exc)
        return []

    rules: list[dict] = []
    for group in parsed.get('groups', []) or []:
        for rule in group.get('rules', []) or []:
            expr = rule.get('expr', '') or ''
            match = _CATEGORY_PATTERN.search(expr)
            if not match:
                continue
            annotations = rule.get('annotations', {}) or {}
            labels = rule.get('labels', {}) or {}
            rules.append(
                {
                    'alert': rule.get('alert', ''),
                    'severity': labels.get('severity', 'info'),
                    'component': labels.get('component', ''),
                    'summary': annotations.get('summary', ''),
                    'category': match.group(1),
                    'expr': expr.strip(),
                }
            )
    return rules


def _rules() -> list[dict]:
    """Return the cached rules; load on first call."""
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules
    with _cache_lock:
        if _cached_rules is None:
            _cached_rules = _load_rules_yaml(_RULES_FILE)
    return _cached_rules


def clear_cache() -> None:
    """Drop the cached rule set. For tests that rewrite the YAML."""
    global _cached_rules
    with _cache_lock:
        _cached_rules = None


def get_active_alerts(error_summary: dict[str, int]) -> list[dict]:
    """Return the list of alerts whose watched category has any hits.

    ``error_summary`` is expected to map category names to counters —
    the same shape the admin dashboard already computes from
    ``app.services.metrics.errors_total``. An alert is considered
    "active" when its category has at least one error since process
    restart. This is coarser than the Prometheus rate threshold but it's
    the honest in-process signal — the widget makes that explicit in the
    template footer.
    """
    active = []
    for rule in _rules():
        count = int(error_summary.get(rule['category'], 0) or 0)
        if count > 0:
            active.append({**rule, 'count': count})
    # Critical first, then warning, then info.
    severity_order = {'critical': 0, 'warning': 1, 'info': 2}
    active.sort(key=lambda r: (severity_order.get(r['severity'], 99), r['alert']))
    return active


def prime_cache() -> None:
    """Parse the YAML once at startup so the first dashboard render is cheap."""
    _rules()
