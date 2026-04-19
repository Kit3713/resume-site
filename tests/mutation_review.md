# Mutation Testing — Survivor Review Log

**Phase:** 18.8 of `ROADMAP_v0.3.0.md`

Living document that tracks surviving mutants from the `mutmut run`
baseline. Every row records either (a) the test we added to kill the
mutant, or (b) an "equivalent mutant" classification with justification
(the mutant's output is observationally identical to the original —
no possible test can tell them apart).

## Baseline Status (2026-04-18)

Initial `mutmut run` attempt with **mutmut 3.5.0** on **Python 3.14.3**
failed at the pytest-invocation boundary:

```
mutmut.__main__.BadTestExecutionCommandsException: Failed to run pytest
with args: ['-ra', '--strict-markers', '--strict-config', '--rootdir=.',
'--tb=native', '-x', '-q', 'tests']
```

Root cause: mutmut 3.x creates a `mutants/` directory containing a copy
of `app/` + `tests/`, then `chdir`s into `mutants/` before invoking
pytest. Our `tests/conftest.py` does `from app import create_app`, which
would resolve to `mutants/app/create_app` — but the `app` package
marker (`__init__.py`) is not properly registered on `sys.path` when
pytest starts under mutmut's wrapper. The import fails and mutmut
aborts before running any mutants.

This is tracked upstream: mutmut 3.x's package-copy model is the
current-but-rough migration from the process-replace model 2.x used.
Until upstream lands a package-discovery fix (or we pin back to 2.x,
which has its own 3.14 compat issues), the baseline will be captured
**manually** via one of:

1. A separate venv with `pip install mutmut==2.4.5` — 2.x worked with
   our project structure but hasn't been verified on 3.14.
2. A small shim `mutants/conftest.py` that prepends `mutants/` to
   `sys.path` so `from app import ...` finds the mutated package.
3. Waiting on the next mutmut release.

Until then, `pyproject.toml [tool.mutmut] paths_to_mutate` is narrowed
to four small leaf modules so that a successful run is tractable in
under five minutes once the import path is unblocked.

**Acceptance note:** Roadmap Phase 18.8 acceptance — "Mutation score
>= 70%" — remains pending. The mutmut config is in place and ready;
the baseline capture is deferred to the next maintenance window.

## Survivor Tracker

| Module | Line | Mutant | Classification | Action |
|---|---|---|---|---|
| — | — | — | — | Baseline not yet captured (see above) |
