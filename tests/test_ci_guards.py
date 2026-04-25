"""
Regression tests for the CI shell guards in ``.github/workflows/ci.yml``.

These tests don't run the GitHub Actions runner — they shell out to ``grep``
with the same expression CI uses and assert it suppresses / fires on the
right lines. Any change to the guard expression in the workflow file must
be mirrored here so the suppression list stays in lock-step with reality.

Phase 28.1 (#29) — the guard now treats both ``# nosec B608`` and
``# noqa: S608`` as accepted suppressions. Either annotation alone is
enough; in practice intentional interpolations in this repo carry both,
but the guard does not require it.
"""

import subprocess

# The exact ``grep -v`` filter the CI workflow uses. If you change the
# regex in ``.github/workflows/ci.yml``, change it here too — these
# tests are the contract.
_SUPPRESSION_REGEX = 'nosec B608|noqa: S608'


def _grep(args, stdin=None):
    """Run ``grep ARGS`` (optionally piping stdin); return stdout.

    S603/S607 are bandit warnings about generic subprocess use; neither
    applies here. The argument list is hardcoded ``grep`` flags + paths
    inside ``tmp_path`` we just wrote, and ``grep`` on PATH is a safe
    assumption in any test environment (CI image, dev workstation).
    """
    return subprocess.run(  # noqa: S603
        ['grep', *args],  # noqa: S607 — grep on PATH is safe in test env.
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _run_guard(scan_dir):
    """
    Run the f-string + .format() halves of the CI guard against a directory.

    Returns the combined stdout. Empty stdout = the guard is clean (no
    findings); non-empty stdout = the guard would fail CI.
    """
    fstring_hits = _grep(['-rn', 'execute(f[\'"]', str(scan_dir)])
    fstring_findings = _grep(['-vE', _SUPPRESSION_REGEX], stdin=fstring_hits)

    format_hits = _grep(['-rn', r'\.format(', str(scan_dir)])
    format_keyworded = _grep(
        ['-i', 'execute\\|select\\|insert\\|update\\|delete'], stdin=format_hits
    )
    format_findings = _grep(['-vE', _SUPPRESSION_REGEX], stdin=format_keyworded)

    return fstring_findings + format_findings


def test_unannotated_fstring_is_flagged(tmp_path):
    """An f-string SQL execute with no annotation should fire the guard."""
    sample = tmp_path / 'unsafe.py'
    sample.write_text("db.execute(f'SELECT * FROM {table}')\n")
    output = _run_guard(tmp_path)
    assert 'unsafe.py' in output, f'Bare f-string execute should fire the guard: {output!r}'


def test_nosec_only_suppresses(tmp_path):
    """A line carrying only ``# nosec B608`` is accepted (legacy style)."""
    sample = tmp_path / 'nosec_only.py'
    sample.write_text("db.execute(f'SELECT * FROM {table}')  # nosec B608 — table from allowlist\n")
    output = _run_guard(tmp_path)
    assert output == '', f'`# nosec B608` alone should suppress the guard, got: {output!r}'


def test_noqa_only_suppresses(tmp_path):
    """
    A line carrying only ``# noqa: S608`` is accepted (Phase 28.1 fix).

    Before the fix the guard only filtered out ``nosec B608``; a future
    contributor who used the ruff/flake8-bandit annotation alone was
    silently un-checked. This is the test that locks the new behaviour.
    """
    sample = tmp_path / 'noqa_only.py'
    sample.write_text("db.execute(f'SELECT * FROM {table}')  # noqa: S608\n")
    output = _run_guard(tmp_path)
    assert output == '', f'`# noqa: S608` alone should suppress the guard, got: {output!r}'


def test_both_annotations_suppresses(tmp_path):
    """The repo's existing convention (both annotations) keeps working."""
    sample = tmp_path / 'both.py'
    sample.write_text("db.execute(f'SELECT * FROM {table}')  # noqa: S608  # nosec B608\n")
    output = _run_guard(tmp_path)
    assert output == '', f'Both annotations together should suppress the guard, got: {output!r}'


def test_format_with_select_keyword_is_flagged(tmp_path):
    """A bare ``.format()`` call near a SQL keyword fires the guard."""
    sample = tmp_path / 'format_unsafe.py'
    sample.write_text("query = 'SELECT * FROM {}'.format(table)\ndb.execute(query)\n")
    output = _run_guard(tmp_path)
    assert 'format_unsafe.py' in output, (
        f'Unannotated .format() with SELECT keyword should fire: {output!r}'
    )


def test_format_with_noqa_is_suppressed(tmp_path):
    """The ``.format()`` half of the guard also honours ``# noqa: S608``."""
    sample = tmp_path / 'format_noqa.py'
    sample.write_text("query = 'SELECT * FROM {}'.format(table)  # noqa: S608\n")
    output = _run_guard(tmp_path)
    assert output == '', f'`# noqa: S608` should suppress the .format() half too, got: {output!r}'


def test_mixed_annotations_in_one_tree(tmp_path):
    """One annotated file, one bare file: the guard fires on exactly the bare one."""
    safe = tmp_path / 'reviewed.py'
    safe.write_text(
        'def a():\n'
        "    db.execute(f'SELECT * FROM {t}')  # noqa: S608\n"
        'def b():\n'
        "    db.execute(f'INSERT INTO {t} VALUES (?)', (v,))  # nosec B608\n"
    )
    unsafe = tmp_path / 'bare.py'
    unsafe.write_text("db.execute(f'DELETE FROM {t}')\n")

    output = _run_guard(tmp_path)
    assert 'bare.py' in output
    assert 'reviewed.py' not in output
