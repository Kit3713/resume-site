# Mutation Testing — Survivor Review Log

**Phase:** 33 of `ROADMAP_v0.3.3.md` (carry-over from v0.3.0 Phase 18.8)

Living document tracking surviving mutants from each `mutmut run`
baseline. Every row records either (a) the test that was added to kill
the mutant, or (b) an "equivalent mutant" pointer to
`tests/MUTATION_EQUIVALENT.md` where the equivalence is justified in
detail.

## Baseline Status (2026-05-17)

Phase 33 captured the scoped four-module baseline (configuration in
`pyproject.toml [tool.mutmut]`). The mutmut 3.5.0 + Python 3.12+
quirks that blocked the original Phase 18.8 attempt are addressed via
two changes:

* `pyproject.toml [tool.mutmut] also_copy` mirrors the rest of the
  `app` package (plus `manage.py`, `schema.sql`, `migrations/`,
  `seeds/`, `docs/`, `config.example.yaml`, `translations/`,
  `babel.cfg`) into `mutants/` so `from app import create_app`
  resolves inside the mutated tree.
* The root-level `conftest.py` carries a `MUTANT_UNDER_TEST`-gated
  shim that makes `multiprocessing.set_start_method` idempotent. The
  mutmut trampoline's `from mutmut.__main__ import
  record_trampoline_hit` would otherwise re-execute the file's
  top-level `set_start_method('fork')` and raise.

### Aggregate

| Metric | Count |
|---|---:|
| Killed | 233 |
| Survived | 47 |
| Timeout | 12 |
| Total | 292 |
| **Kill rate** | **79.8%** (233 / (233+47+12)) |

Target ≥ 70% — met.

### Per-module

| Module | Mutants | Killed | Survived | Timeout | Kill rate |
|---|---:|---:|---:|---:|---:|
| `app/services/text.py` | 31 | 30 | 1 | 0 | 96.8% |
| `app/services/pagination.py` | 47 | 44 | 3 | 0 | 93.6% |
| `app/services/time_helpers.py` | 83 | 70 | 13 | 0 | 84.3% |
| `app/services/login_throttle.py` | 131 | 89 | 30 | 12 | 67.9% |

`login_throttle.py` sits below the 70% target. Most survivors are
constant-boundary mutations (e.g. `LIMIT 1000` → `LIMIT 1001`) and
timestamp-arithmetic edges in `record_failed_login`,
`record_successful_login`, `check_lockout`, and `purge_old_attempts`.
These get killed in Phase 34's edge-case retroactive pass.

## Survivor Tracker

Status legend:

* **kill**: a test was added that converts this mutant from `survived`
  to `killed` on the next baseline run. List the test name.
* **equivalent**: the mutant is observationally identical to the
  original; see `tests/MUTATION_EQUIVALENT.md` for the rubric and the
  detailed justification.
* **pending**: not yet classified — needs review.

| Module | Mutant | Function | Status | Notes |
|---|---|---|---|---|
| `app/services/text.py` | `slugify__mutmut_31` | `slugify` | pending | text-normalisation edge case; investigate during Phase 34. |
| `app/services/pagination.py` | `paginate__mutmut_2` | `paginate` | pending | Pagination boundary; needs targeted test. |
| `app/services/pagination.py` | `paginate__mutmut_23` | `paginate` | pending | Page-size boundary. |
| `app/services/pagination.py` | `paginate__mutmut_25` | `paginate` | pending | Page-size boundary. |
| `app/services/time_helpers.py` | `_parse_iso__mutmut_*` (1, 4, 17, 18) | `_parse_iso` | pending | ISO-8601 parsing edges; some likely equivalent (UTC tz suffix variants). |
| `app/services/time_helpers.py` | `time_ago__mutmut_*` (7-11, 15-16, 42, 48) | `time_ago` | pending | Bucket-boundary arithmetic; Phase 34 edge tests. |
| `app/services/login_throttle.py` | `_inc_login_attempts__mutmut_*` (1, 2) | `_inc_login_attempts` | pending | SQL-statement variants. |
| `app/services/login_throttle.py` | `record_failed_login__mutmut_*` (6, 7, 10-12) | `record_failed_login` | pending | Timestamp-arithmetic. |
| `app/services/login_throttle.py` | `record_successful_login__mutmut_*` (6, 7, 9-12) | `record_successful_login` | pending | Timestamp-arithmetic. |
| `app/services/login_throttle.py` | `check_lockout__mutmut_*` (4, 5, 7, 14, 30, 32, 65, 67, 73-76) | `check_lockout` | pending | Lockout-window boundaries. |
| `app/services/login_throttle.py` | `purge_old_attempts__mutmut_*` (1, 2, 6, 14, 15) | `purge_old_attempts` | pending | Retention-cutoff arithmetic. |

Every "pending" row carries forward into Phase 34. The acceptance
criteria for the v0.3.3 release: 70% kill rate captured (met at
79.8%). The acceptance criteria for the v0.4.x ratchet: zero pending
rows, all survivors either killed or moved to
`tests/MUTATION_EQUIVALENT.md`.
