# Equivalent-Mutant Catalogue

**Phase:** 33 of `ROADMAP_v0.3.3.md` (carry-over from v0.3.0 Phase 18.8)

Living register of mutants that survived `mutmut run` yet cannot be killed
by any possible test because their output is observationally identical to
the original. Every row carries a one-line justification so future readers
do not waste time trying to write a test that, by definition, cannot
distinguish the two implementations.

The companion log `tests/mutation_review.md` tracks survivors that *were*
killed (which test was added, when) and the broader baseline status. This
file is the narrower "equivalent-mutant" register — entries here are
permanent; entries there roll off as soon as a test lands.

## How to use

1. Run `mutmut run` (configuration in `pyproject.toml`).
2. Run `python manage.py mutation-report` to get the survivor list.
3. For each survivor, decide:
   * **Killable?** Add a test that distinguishes the mutant. Log the
     decision in `tests/mutation_review.md` once green.
   * **Equivalent?** Add a row below with a short justification. The
     mutant lives forever; the test suite has no obligation to kill it.
4. Re-run `mutmut run` and confirm the survivor list shrinks to only the
   equivalent set documented here.

## Classification rubric

Common equivalence patterns we accept without further test work:

| Pattern | Example | Why it is equivalent |
|---|---|---|
| Dead default | `x or 'foo'` → `x or 'bar'` where `x` is always truthy in production | The fallback branch is unreachable. |
| Type-system constraint | Type checker rejects the mutant before it can run | Already caught by `caught_by_type_check`. |
| Comment / docstring mutation | Whitespace inside a `"""docstring"""` | Compiler-equivalent. |
| Logging-only mutation | `logger.info('OK')` → `logger.info('XX')` | No observable effect on caller. |
| Constant-folded redundancy | `int(int(x))` → `int(x)` | Compiler / interpreter elides one call. |
| Sentinel re-assignment | `_SENTINEL = object()` → `_SENTINEL = ()` where `_SENTINEL` is only used for `is`-comparison | `is`-identity preserved by both. |

Anything that does not match a documented pattern needs a kill, not an
equivalence row. When in doubt, write the test.

## Equivalent mutants

| Module | Mutant | Pattern | Justification |
|---|---|---|---|
| _none yet_ | — | — | Baseline run has not produced survivors classified as equivalent. |

## Ratchet plan

* **v0.3.3:** Capture the baseline subset (4 leaf modules in
  `pyproject.toml [tool.mutmut]`). Populate this table from real
  survivors.
* **v0.4.x:** Expand `paths_to_mutate` to the hot-path modules listed in
  the Phase 33 roadmap entry. New equivalence claims must point at a
  pattern above or extend the rubric.
* **v0.5.x or later:** Once the baseline is stable two cycles in a row,
  ratchet the mutation kill rate from "advisory" to "blocking" in
  `.github/workflows/mutation.yml`. Equivalence rows must be reviewed
  during release prep — drift means an obsolete claim.
