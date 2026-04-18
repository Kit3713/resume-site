"""
Prometheus-compatible Metrics — Phase 18.2

Lightweight metrics registry that emits the Prometheus text exposition
format (``text/plain; version=0.0.4``). Deliberately avoids the
``prometheus_client`` package — the roadmap calls for a stdlib-only
implementation because this app's metric surface is small and the extra
supply-chain surface isn't worth the convenience.

Registry model:
    * A module-level :class:`MetricsRegistry` singleton (:data:`_registry`)
      holds every metric. Metrics are declared at import time and updated
      from request-handling hooks.
    * Three primitives: :class:`Counter`, :class:`Gauge`,
      :class:`Histogram`. Each is labelled (``method``, ``path``,
      ``status`` etc.). Thread-safe via a single registry-wide lock —
      contention is irrelevant at this app's scale.
    * :func:`render` produces the exposition text. Consumed by the
      ``/metrics`` route.

Privacy / cardinality notes:
    * Path labels use :attr:`request.url_rule.rule` (e.g. ``/blog/<slug>``)
      rather than the raw path. Unmatched requests (404) use the constant
      ``<unmatched>`` so an attacker can't blow up cardinality by probing
      random URLs.
    * The ``/metrics`` endpoint self-excludes from request metrics so a
      high scrape rate doesn't dominate request counters.

No third-party dependencies — ``math`` and ``threading`` from the
standard library only.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default histogram buckets (seconds). Wide enough to cover fast 404s and
# slow photo uploads. Taken from the Phase 18.2 roadmap bullet.
DEFAULT_DURATION_BUCKETS = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)

# Content-Type required by the Prometheus text exposition format.
CONTENT_TYPE = 'text/plain; version=0.0.4; charset=utf-8'

# Used when the incoming request didn't match any Flask URL rule (e.g. 404
# on a probed path). Avoids unbounded label cardinality.
UNMATCHED_PATH = '<unmatched>'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_labels(labels):
    """Render a label dict as the Prometheus in-line label string.

    ``{'method': 'GET', 'status': '200'}`` →
    ``{method="GET",status="200"}``.
    Empty dict → empty string.
    """
    if not labels:
        return ''
    parts = []
    for key in sorted(labels):
        value = str(labels[key])
        # Escape per the exposition format: \, newline, and quote.
        escaped = value.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')
        parts.append(f'{key}="{escaped}"')
    return '{' + ','.join(parts) + '}'


def _format_value(value):
    """Render a numeric value. NaN and infinities are valid in Prometheus text."""
    if math.isnan(value):
        return 'NaN'
    if math.isinf(value):
        return '+Inf' if value > 0 else '-Inf'
    # Integers print without decimal for readability; floats use repr (shortest roundtrip).
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return repr(float(value))


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class _Metric:
    """Shared state for all metric types."""

    TYPE = 'untyped'

    def __init__(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> None:
        """Store the metric identity; subclasses own the value semantics."""
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        # {label_values_tuple: value}
        self._values: dict[tuple, float] = {}

    def _key(self, label_values: Iterable) -> tuple:
        if len(label_values) != len(self.label_names):
            raise ValueError(
                f'{self.name}: expected {len(self.label_names)} label values, '
                f'got {len(label_values)}'
            )
        return tuple(str(v) for v in label_values)

    def samples(self):
        """Yield (metric_name, label_dict, value) triples for rendering."""
        for key, value in self._values.items():
            labels = dict(zip(self.label_names, key, strict=True))
            yield (self.name, labels, value)


class Counter(_Metric):
    """Monotonically increasing counter.

    Counters only go up (they reset to 0 when the process restarts —
    Prometheus's rate() handles that). Calling :meth:`inc` with a
    negative value is an error.
    """

    TYPE = 'counter'

    def inc(self, label_values: Iterable = (), amount: float = 1) -> None:
        """Add ``amount`` to the series keyed by ``label_values``.

        Raises ``ValueError`` if ``amount`` is negative — counters are
        monotonic and a subtraction would lie to ``rate()``.
        """
        if amount < 0:
            raise ValueError(f'{self.name}.inc() amount must be non-negative')
        key = self._key(label_values)
        self._values[key] = self._values.get(key, 0) + amount


class Gauge(_Metric):
    """Arbitrary-value gauge."""

    TYPE = 'gauge'

    def set(self, value: float, label_values: Iterable = ()) -> None:
        """Overwrite the gauge series for ``label_values`` with ``value``."""
        self._values[self._key(label_values)] = float(value)

    def inc(self, label_values: Iterable = (), amount: float = 1) -> None:
        """Add ``amount`` to the gauge series for ``label_values``."""
        key = self._key(label_values)
        self._values[key] = self._values.get(key, 0) + amount

    def dec(self, label_values: Iterable = (), amount: float = 1) -> None:
        """Subtract ``amount`` from the gauge series for ``label_values``."""
        self.inc(label_values, -amount)


class Histogram(_Metric):
    """Cumulative histogram with configurable buckets.

    Observations falling within each bucket increment all buckets at or
    above the observed value. A synthetic ``+Inf`` bucket catches
    everything. ``_sum`` and ``_count`` accompany the buckets per
    Prometheus convention.
    """

    TYPE = 'histogram'

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: Iterable[float] | None = None,
    ) -> None:
        """Build the histogram with its immutable sorted bucket ladder."""
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(sorted(buckets or DEFAULT_DURATION_BUCKETS))
        # For histograms, _values holds buckets / sum / count keyed by
        # (label_values, kind). kind is either a bucket upper bound
        # (float) or the string 'sum' / 'count'.
        self._values = {}

    def observe(self, value: float, label_values: Iterable = ()) -> None:
        """Record ``value`` in the matching series — bumps all buckets >= value, plus ``_sum`` / ``_count``."""
        key = self._key(label_values)
        for ub in self.buckets:
            if value <= ub:
                self._values[(key, ub)] = self._values.get((key, ub), 0) + 1
        self._values[(key, '+Inf')] = self._values.get((key, '+Inf'), 0) + 1
        self._values[(key, 'sum')] = self._values.get((key, 'sum'), 0) + value
        self._values[(key, 'count')] = self._values.get((key, 'count'), 0) + 1

    def samples(self):
        """Yield exposition triples: one bucket ladder + ``_sum`` + ``_count`` per series."""
        # Group samples per label_values so each histogram series gets
        # its bucket ladder + sum + count emitted together.
        seen_labels = {}
        for (label_key, _), _ in self._values.items():
            seen_labels[label_key] = None

        for label_key in seen_labels:
            labels = dict(zip(self.label_names, label_key, strict=True))
            for ub in [*list(self.buckets), '+Inf']:
                bucket_labels = dict(labels)
                bucket_labels['le'] = '+Inf' if ub == '+Inf' else repr(float(ub))
                value = self._values.get((label_key, ub), 0)
                yield (f'{self.name}_bucket', bucket_labels, value)
            yield (f'{self.name}_sum', labels, self._values.get((label_key, 'sum'), 0))
            yield (f'{self.name}_count', labels, self._values.get((label_key, 'count'), 0))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Thread-safe registry of :class:`_Metric` instances.

    Use :meth:`counter`, :meth:`gauge`, :meth:`histogram` to declare
    metrics (idempotent — redeclaring the same name returns the existing
    instance as long as the type matches, otherwise raises). Call
    :meth:`render` to produce the exposition text.
    """

    def __init__(self) -> None:
        """Create an empty registry with its own lock."""
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> Counter:
        """Declare or return the :class:`Counter` registered under ``name``."""
        return self._get_or_create(name, help_text, label_names, Counter)

    def gauge(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> Gauge:
        """Declare or return the :class:`Gauge` registered under ``name``."""
        return self._get_or_create(name, help_text, label_names, Gauge)

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: Iterable[float] | None = None,
    ) -> Histogram:
        """Declare or return the :class:`Histogram` registered under ``name``."""
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, Histogram):
                    raise TypeError(f'{name!r} already registered as {type(existing).__name__}')
                return existing
            metric = Histogram(name, help_text, label_names, buckets=buckets)
            self._metrics[name] = metric
            return metric

    def _get_or_create(self, name, help_text, label_names, cls):
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, cls):
                    raise TypeError(f'{name!r} already registered as {type(existing).__name__}')
                return existing
            metric = cls(name, help_text, label_names)
            self._metrics[name] = metric
            return metric

    def reset(self) -> None:
        """Drop all metrics. Test-only — production never calls this."""
        with self._lock:
            self._metrics.clear()

    def render(self) -> str:
        """Return the exposition-format text for all registered metrics.

        Acquires the registry lock for the duration of the render so
        concurrent updates don't interleave into a malformed line.
        """
        with self._lock:
            lines = []
            for metric in self._metrics.values():
                lines.append(f'# HELP {metric.name} {metric.help}')
                lines.append(f'# TYPE {metric.name} {metric.TYPE}')
                for sample_name, sample_labels, sample_value in metric.samples():
                    lines.append(
                        f'{sample_name}{_format_labels(sample_labels)} '
                        f'{_format_value(sample_value)}'
                    )
            # Prometheus requires a trailing newline.
            return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Module-level registry + process uptime anchor
# ---------------------------------------------------------------------------

_registry = MetricsRegistry()
_process_start = time.monotonic()


def get_registry() -> MetricsRegistry:
    """Return the process-wide :class:`MetricsRegistry`."""
    return _registry


def process_uptime_seconds() -> float:
    """Return seconds elapsed since this module was imported."""
    return time.monotonic() - _process_start


# ---------------------------------------------------------------------------
# Declared metrics (import-time)
# ---------------------------------------------------------------------------

requests_total = _registry.counter(
    'resume_site_requests_total',
    'Total HTTP requests handled, labelled by method, route template, and status code.',
    label_names=('method', 'path', 'status'),
)

request_duration_seconds = _registry.histogram(
    'resume_site_request_duration_seconds',
    'HTTP request handling duration in seconds, labelled by method and route template.',
    label_names=('method', 'path'),
)

uptime_seconds = _registry.gauge(
    'resume_site_uptime_seconds',
    'Seconds since this process started serving.',
)

errors_total = _registry.counter(
    'resume_site_errors_total',
    'Total errors by operational category and response status code. See app/errors.py.',
    label_names=('category', 'status'),
)

# --- Domain-specific metrics (Phase 18.2 deferred batch) ---

photo_uploads_total = _registry.counter(
    'resume_site_photo_uploads_total',
    'Total photo uploads processed (admin UI + API).',
)

contact_submissions_total = _registry.counter(
    'resume_site_contact_submissions_total',
    'Total contact form submissions by spam status.',
    label_names=('is_spam',),
)

blog_posts_total = _registry.gauge(
    'resume_site_blog_posts_total',
    'Current number of blog posts by status (computed at scrape time).',
    label_names=('status',),
)

backup_last_success_timestamp = _registry.gauge(
    'resume_site_backup_last_success_timestamp',
    'Unix epoch of the most recent successful backup (from settings table).',
)

disk_usage_bytes = _registry.gauge(
    'resume_site_disk_usage_bytes',
    'Disk usage in bytes for key storage paths.',
    label_names=('path',),
)


# ---------------------------------------------------------------------------
# Request instrumentation helper
# ---------------------------------------------------------------------------


def record_request(
    method: str, url_rule: str | None, status_code: int, duration_seconds: float
) -> None:
    """Update request metrics for a single request.

    Args:
        method: HTTP method (``GET``, ``POST``, ...).
        url_rule: A Flask rule string (``/blog/<slug>``) or ``None`` for
            unmatched requests (404 probes). ``None`` is normalised to
            :data:`UNMATCHED_PATH` to bound label cardinality.
        status_code: Final HTTP status.
        duration_seconds: Elapsed wall-clock time in seconds (float).
    """
    path = url_rule or UNMATCHED_PATH
    requests_total.inc(label_values=(method, path, str(status_code)))
    request_duration_seconds.observe(duration_seconds, label_values=(method, path))


# ---------------------------------------------------------------------------
# Access-control helper (shared between the /metrics route and tests)
# ---------------------------------------------------------------------------


def client_ip_in_networks(client_ip_str: str | None, networks: Iterable[str]) -> bool:
    """Return True if ``client_ip_str`` is inside any of ``networks``.

    Args:
        client_ip_str: The client IP string (may be ``None`` or empty).
        networks: Iterable of CIDR strings. An empty iterable returns
            False — the caller decides the default policy.
    """
    if not client_ip_str:
        return False
    try:
        client_ip = ipaddress.ip_address(client_ip_str)
    except (ValueError, TypeError):
        return False
    for cidr in networks:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if client_ip in net:
            return True
    return False


def parse_cidr_list(raw: str) -> list[str]:
    """Split a comma-separated CIDR string from a settings value."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(',') if piece.strip()]
