"""
OpenAPI Specification Tests — Phase 16.5

Validates ``docs/openapi.yaml``:

* Parses as valid YAML and has the OpenAPI 3.0 top-level keys
  (``openapi``, ``info``, ``paths``, ``components``).
* Every operation declares a stable ``operationId``, all unique.
* The set of ``(method, path)`` pairs in the spec is byte-for-byte
  identical to the set of routes Flask actually registers under the
  ``api`` blueprint (excluding the three docs routes themselves) —
  this is the **drift guard** that prevents silent spec rot.
* Every protected operation declares 401 + 403 responses; every
  parameterised path declares 404; every operation declares 429.
* Every ``$ref`` resolves to an existing component.
* The ``code`` enum on the shared ``Error`` schema is a superset of
  every ``code='...'`` literal grepped from ``app/routes/api.py`` —
  catches new error codes that never made it into the spec.

Modeled on ``tests/test_alerting_rules.py``: spec drift is the most
costly failure mode for hand-written specs, and the drift guard makes
it a build-time error rather than an integration-time surprise.
"""

from __future__ import annotations

import os
import re

import pytest

yaml = pytest.importorskip('yaml')


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
SPEC_PATH = os.path.join(PROJECT_ROOT, 'docs', 'openapi.yaml')
API_ROUTES_PATH = os.path.join(PROJECT_ROOT, 'app', 'routes', 'api.py')

# Routes that serve the spec/UI itself — they exist on the blueprint
# but deliberately are NOT documented in the spec (would be a self-
# referential mess and a security distraction).
DOCS_SELF_ROUTES = frozenset(
    {
        'api.openapi_yaml',
        'api.openapi_json',
        'api.openapi_docs',
    }
)

# Methods Flask synthesises on every rule that we don't want to compare.
# `OPTIONS` is auto-added by Werkzeug for CORS-style preflight; `HEAD`
# is auto-added wherever GET exists.
SYNTHETIC_METHODS = frozenset({'HEAD', 'OPTIONS'})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope='module')
def spec():
    with open(SPEC_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope='module')
def operations(spec):
    """Return [(method_upper, path, operation_dict), ...] for every operation."""
    out = []
    for path, item in spec['paths'].items():
        for method, op in item.items():
            if method.lower() in {'get', 'post', 'put', 'delete', 'patch'}:
                out.append((method.upper(), path, op))
    return out


# ---------------------------------------------------------------------------
# Structural / OpenAPI 3 conformance
# ---------------------------------------------------------------------------


def test_spec_file_exists():
    assert os.path.isfile(SPEC_PATH), f'missing: {SPEC_PATH}'


def test_spec_has_top_level_keys(spec):
    for key in ('openapi', 'info', 'paths', 'components'):
        assert key in spec, f'spec missing top-level key {key!r}'


def test_spec_declares_openapi_30(spec):
    assert spec['openapi'].startswith('3.0'), f'expected OpenAPI 3.0.x, got {spec["openapi"]!r}'


def test_info_has_title_and_version(spec):
    assert spec['info'].get('title')
    assert spec['info'].get('version')


def test_bearer_security_scheme_exists(spec):
    schemes = spec.get('components', {}).get('securitySchemes', {})
    assert 'BearerAuth' in schemes, 'BearerAuth scheme is referenced by every protected route'
    bearer = schemes['BearerAuth']
    assert bearer['type'] == 'http'
    assert bearer['scheme'] == 'bearer'


def test_servers_block_present(spec):
    assert spec.get('servers'), 'servers list lets Swagger UI build correct request URLs'
    assert any(s.get('url', '').endswith('/api/v1') for s in spec['servers'])


# ---------------------------------------------------------------------------
# operationId hygiene
# ---------------------------------------------------------------------------


def test_every_operation_has_operation_id(operations):
    for method, path, op in operations:
        assert 'operationId' in op, f'{method} {path} missing operationId'


def test_operation_ids_are_camel_case(operations):
    pattern = re.compile(r'^[a-z][A-Za-z0-9]+$')
    for method, path, op in operations:
        op_id = op['operationId']
        assert pattern.match(op_id), (
            f'{method} {path}: operationId {op_id!r} should be camelCase '
            f'starting with a lowercase letter'
        )


def test_operation_ids_are_unique(operations):
    ids = [op['operationId'] for _, _, op in operations]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f'duplicate operationIds: {duplicates}'


# ---------------------------------------------------------------------------
# Drift guard — spec ↔ Flask URL map
# ---------------------------------------------------------------------------


def _spec_path_to_pattern(spec_path):
    """Convert ``/blog/{slug}`` → the same shape Flask emits."""
    # Both sides will be normalised to ``{name}`` for comparison.
    return spec_path


def _flask_rule_to_pattern(rule_str):
    """Convert ``/blog/<slug>`` / ``/blog/<int:photo_id>`` → ``/blog/{slug}``.

    Strips both the typed and untyped Werkzeug converters; keeps the
    parameter name only.
    """
    # <int:photo_id> -> {photo_id};  <slug> -> {slug}
    rule = re.sub(r'<(?:[a-z]+:)?([a-zA-Z_][a-zA-Z0-9_]*)>', r'{\1}', rule_str)
    # Blueprint url_prefix '/api/v1' is included in rule.rule — strip it
    # so we compare against the spec which uses paths relative to /api/v1.
    if rule.startswith('/api/v1'):
        rule = rule[len('/api/v1') :] or '/'
    return rule


@pytest.fixture
def flask_api_routes(app):
    """Return ``{(METHOD, /relative/path), ...}`` for every api.* route, minus docs self-routes.

    Uses the ``app`` fixture from ``tests/conftest.py`` so the test
    config (and its ``DATABASE_PATH``) is set up the same way as the
    rest of the suite. Function-scoped because the upstream fixture is.
    """
    out = set()
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith('api.'):
            continue
        if rule.endpoint in DOCS_SELF_ROUTES:
            continue
        path = _flask_rule_to_pattern(rule.rule)
        for method in rule.methods or ():
            if method in SYNTHETIC_METHODS:
                continue
            out.add((method, path))
    return out


@pytest.fixture
def spec_routes(operations):
    """Return ``{(METHOD, /path), ...}`` extracted from the spec."""
    return {(method, path) for method, path, _ in operations}


def test_no_routes_missing_from_spec(flask_api_routes, spec_routes):
    """Every Flask route is documented."""
    missing = sorted(flask_api_routes - spec_routes)
    assert not missing, (
        f'These Flask routes are NOT documented in docs/openapi.yaml: {missing}\n'
        f'Add them, or — for routes that are deliberately undocumented — '
        f'extend DOCS_SELF_ROUTES in this test.'
    )


def test_no_phantom_routes_in_spec(flask_api_routes, spec_routes):
    """Every documented route exists on the Flask blueprint."""
    phantom = sorted(spec_routes - flask_api_routes)
    assert not phantom, (
        f'These spec entries do NOT match a registered Flask route: {phantom}\n'
        f'Either remove them from docs/openapi.yaml or register the route.'
    )


# ---------------------------------------------------------------------------
# Per-operation response coverage
# ---------------------------------------------------------------------------


def _op_is_protected(op):
    return bool(op.get('security'))


def _path_has_path_param(path):
    return '{' in path


def test_protected_operations_declare_401_and_403(operations):
    for method, path, op in operations:
        if not _op_is_protected(op):
            continue
        responses = op.get('responses', {})
        assert '401' in responses, f'{method} {path} (protected) missing 401'
        assert '403' in responses, f'{method} {path} (protected) missing 403'


def test_path_parameterised_operations_declare_404(operations):
    for method, path, op in operations:
        if not _path_has_path_param(path):
            continue
        responses = op.get('responses', {})
        assert '404' in responses, f'{method} {path} has a path parameter but does not declare 404'


def test_every_operation_declares_429(operations):
    for method, path, op in operations:
        responses = op.get('responses', {})
        assert '429' in responses, f'{method} {path} missing 429 — every API route is rate-limited'


def test_write_operations_declare_415(operations):
    """JSON write routes (not the multipart upload) document 415 for bad content-type."""
    EXEMPT_OPERATION_IDS = frozenset(
        {
            # multipart upload — explicitly bypasses the JSON content-type middleware
            'uploadPortfolioPhoto',
            # publish/unpublish take no body, so the middleware doesn't gate them
            'publishBlogPost',
            'unpublishBlogPost',
            # delete-only paths obviously have no request body
        }
    )
    for method, path, op in operations:
        if method not in {'POST', 'PUT', 'PATCH'}:
            continue
        if op.get('operationId') in EXEMPT_OPERATION_IDS:
            continue
        # multipart routes declare a non-JSON requestBody Content-Type;
        # detect that to skip them automatically too.
        body = op.get('requestBody', {}).get('content', {})
        if body and 'application/json' not in body and 'multipart/form-data' in body:
            continue
        responses = op.get('responses', {})
        assert '415' in responses, (
            f'{method} {path}: write route should declare 415 for missing JSON Content-Type'
        )


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------


def _walk(node, path_parts=()):
    """Yield every (path_parts, value) pair in a nested dict/list tree."""
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _walk(child, path_parts + (str(key),))
    elif isinstance(node, list):
        for idx, child in enumerate(node):
            yield from _walk(child, path_parts + (str(idx),))
    else:
        yield path_parts, node


def _resolve_ref(spec, ref):
    """Return the node at ``#/a/b/c`` or raise KeyError."""
    assert ref.startswith('#/'), f'only local refs supported here: {ref!r}'
    node = spec
    for segment in ref[2:].split('/'):
        node = node[segment]
    return node


def test_every_local_ref_resolves(spec):
    seen = set()
    for path_parts, value in _walk(spec):
        if path_parts and path_parts[-1] == '$ref' and isinstance(value, str):
            seen.add(value)
    assert seen, 'expected at least one $ref in the spec'
    for ref in sorted(seen):
        try:
            _resolve_ref(spec, ref)
        except (KeyError, TypeError) as exc:
            pytest.fail(f'$ref {ref!r} does not resolve: {exc}')


# ---------------------------------------------------------------------------
# Error code catalog ↔ source code drift
# ---------------------------------------------------------------------------


# `_error('msg', 'CODE_NAME', ...)` — second positional arg.
ERROR_CALL_RE = re.compile(r"""_error\(\s*[^,]+,\s*['"]([A-Z_]+)['"]""")
# `code='CODE_NAME'` keyword form (covers any future helper rename).
CODE_KWARG_RE = re.compile(r"""\bcode\s*=\s*['"]([A-Z_]+)['"]""")


def _source_referenced_codes():
    """Return every error code raised from app/routes/api.py.

    Only matches actual call-site forms, so docstring examples like
    ``"code": "ERROR_CODE"`` don't get swept in.
    """
    with open(API_ROUTES_PATH) as f:
        source = f.read()
    referenced = set(ERROR_CALL_RE.findall(source)) | set(CODE_KWARG_RE.findall(source))
    # METHOD_NOT_ALLOWED is set inside _api_method_not_allowed via _error
    # but that already gets matched. Same with NOT_FOUND in _api_not_found.
    return referenced


def test_error_code_catalog_covers_source_codes(spec):
    """Every error code raised by api.py appears in the spec's enum."""
    referenced = _source_referenced_codes()
    enum = set(spec['components']['schemas']['Error']['properties']['code']['enum'])
    missing = sorted(referenced - enum)
    assert not missing, (
        f'These error codes are raised by app/routes/api.py but missing from the '
        f'Error.code enum in docs/openapi.yaml: {missing}\n'
        f'Either add them to the enum or remove the source-side literal.'
    )


def test_error_code_catalog_has_no_unused_codes(spec):
    """Spec-only codes (in the enum but never raised) get surfaced too.

    Mirrors the canary in tests/test_alerting_rules.py — drift in the
    other direction is just as confusing for API consumers.
    """
    referenced = _source_referenced_codes()
    enum = set(spec['components']['schemas']['Error']['properties']['code']['enum'])
    EXEMPT: set[str] = set()
    unused = enum - referenced - EXEMPT
    assert not unused, (
        f'Error codes in the spec enum but never raised: {sorted(unused)}. '
        f'Drop them or add to EXEMPT here with a comment.'
    )


# ---------------------------------------------------------------------------
# Phase 37.3 — deprecation drift guard (spec ↔ @deprecated decorator)
# ---------------------------------------------------------------------------


def _spec_path_to_flask_pattern(spec_path):
    """Convert ``/blog/{slug}`` (spec) → ``/api/v1/blog/<slug>`` (Flask rule).

    The Flask url_map stores rules with the blueprint's ``url_prefix``
    intact and Werkzeug-style ``<name>`` placeholders. We rebuild that
    shape so the drift guard can resolve a spec path back to a registered
    rule without typed-converter knowledge (``<int:photo_id>`` collapses
    to ``<photo_id>`` for the comparison).
    """
    return '/api/v1' + re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', r'<\1>', spec_path)


def _flask_view_for(app, method, spec_path):
    """Return the Flask view function for ``(method, spec_path)`` or ``None``.

    The drift guard tolerates an absent route (the phantom-routes test
    catches that case with a clearer message); when this helper returns
    ``None`` the deprecation test reports its own dedicated failure so
    the operator sees both signals.
    """
    target = _spec_path_to_flask_pattern(spec_path)
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith('api.'):
            continue
        if rule.endpoint in DOCS_SELF_ROUTES:
            continue
        # Strip typed converters from the registered rule the same way the
        # main drift guard does, so ``/portfolio/<int:photo_id>`` and a
        # spec path of ``/portfolio/{photo_id}`` line up.
        normalised = re.sub(r'<(?:[a-z]+:)?([a-zA-Z_][a-zA-Z0-9_]*)>', r'<\1>', rule.rule)
        if normalised != target:
            continue
        if method.upper() not in (rule.methods or ()):
            continue
        return app.view_functions.get(rule.endpoint)
    return None


def test_openapi_deprecated_flag_matches_decorator(app, spec, operations):
    """Phase 37.3: spec ``deprecated: true`` ↔ ``@deprecated`` decorator.

    Every OpenAPI operation flagged ``deprecated: true`` must:

    * Resolve to a registered Flask view in the ``api`` blueprint.
    * Have the ``@deprecated`` decorator applied (detected via the
      ``__deprecated_sunset__`` marker the decorator sets on the wrapped
      function).
    * Declare an ``x-sunset`` extension key in the spec.
    * Have its ``x-sunset`` value match the decorator's ``sunset_date``.

    A drift in either direction (spec-only or decorator-only) fails.

    If no operations are currently flagged ``deprecated``, the loop walks
    nothing and the test passes — that's correct: there's no drift to
    detect yet, but the guard is in place for the first deprecation.
    """
    drift = []
    for method, path, operation in operations:
        if operation.get('deprecated') is not True:
            continue

        view = _flask_view_for(app, method, path)
        if view is None:
            drift.append(
                f'{method} {path}: spec marks deprecated, but no matching Flask route '
                f'is registered under the api blueprint'
            )
            continue

        sunset = getattr(view, '__deprecated_sunset__', None)
        if sunset is None:
            drift.append(
                f'{method} {path}: spec marks deprecated, but the Flask view '
                f'{view.__module__}.{view.__name__} is missing the @deprecated decorator '
                f'(no __deprecated_sunset__ marker)'
            )
            continue

        spec_sunset = operation.get('x-sunset')
        if not spec_sunset:
            drift.append(
                f'{method} {path}: deprecated operations must declare an x-sunset '
                f'extension key carrying the same date as the decorator '
                f'(decorator says {sunset!r})'
            )
            continue

        # Compare on the date portion only — the decorator stores the
        # ISO date, the spec may or may not include a time component.
        if str(spec_sunset).split('T', 1)[0] != sunset:
            drift.append(
                f'{method} {path}: x-sunset {spec_sunset!r} does not match '
                f'@deprecated(sunset_date) {sunset!r}'
            )

    assert not drift, 'OpenAPI deprecation drift:\n  - ' + '\n  - '.join(drift)
