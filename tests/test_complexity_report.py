"""
Complexity Report Tests — Phase 12.5

Verifies the `manage.py complexity-report` command:
- `_cyclomatic_complexity` scores a broad set of branching constructs correctly.
- Nested functions and classes are reported as independent entries.
- `_analyze_file` handles malformed source files without aborting.
- The CLI command prints a well-formed report and honours `--top`.
- Invalid `--top` values exit non-zero.
"""

import argparse
import ast
import os
import re

import pytest

from manage import (
    _analyze_file,
    _cyclomatic_complexity,
    _iter_python_files,
    complexity_report,
)

# ---------------------------------------------------------------------------
# _cyclomatic_complexity
# ---------------------------------------------------------------------------


def _parse_function(source):
    """Parse `source` and return the first top-level function node."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError(f'No function in source: {source!r}')


def test_complexity_trivial_function_is_one():
    fn = _parse_function('def f():\n    return 1\n')
    assert _cyclomatic_complexity(fn) == 1


def test_complexity_single_if():
    fn = _parse_function('def f(x):\n    if x:\n        return 1\n    return 0\n')
    assert _cyclomatic_complexity(fn) == 2


def test_complexity_if_elif_else():
    fn = _parse_function(
        'def f(x):\n'
        '    if x == 1:\n'
        '        return 1\n'
        '    elif x == 2:\n'
        '        return 2\n'
        '    else:\n'
        '        return 3\n'
    )
    # The elif desugars to a nested If node, so two If branches total
    assert _cyclomatic_complexity(fn) == 3


def test_complexity_for_with_nested_if():
    fn = _parse_function('def f(xs):\n    for x in xs:\n        if x:\n            print(x)\n')
    assert _cyclomatic_complexity(fn) == 3


def test_complexity_try_with_two_handlers():
    fn = _parse_function(
        'def f():\n'
        '    try:\n'
        '        do()\n'
        '    except ValueError:\n'
        '        pass\n'
        '    except KeyError:\n'
        '        pass\n'
    )
    # Base 1 + two ExceptHandlers = 3
    assert _cyclomatic_complexity(fn) == 3


def test_complexity_bool_op_chain():
    fn = _parse_function('def f(a, b, c):\n    if a and b and c:\n        return 1\n')
    # 1 base + 1 If + 2 extra BoolOp operands = 4
    assert _cyclomatic_complexity(fn) == 4


def test_complexity_comprehension_with_two_filters():
    fn = _parse_function('def f(xs):\n    return [x for x in xs if x if x > 0]\n')
    # 1 base + 1 for + 2 ifs = 4
    assert _cyclomatic_complexity(fn) == 4


def test_complexity_while_break_is_two():
    fn = _parse_function('def f():\n    while True:\n        break\n')
    # break is not a branching node
    assert _cyclomatic_complexity(fn) == 2


def test_complexity_async_function_same_as_sync():
    fn = _parse_function('async def f(x):\n    if x:\n        return 1\n    return 0\n')
    assert _cyclomatic_complexity(fn) == 2


def test_complexity_excludes_nested_function_body():
    """Outer's complexity must not count branches inside an inner function."""
    source = (
        'def outer(x):\n'
        '    def inner(y):\n'
        '        if y:\n'
        '            if y > 0:\n'
        '                return 1\n'
        '    if x:\n'
        '        return inner(x)\n'
        '    return 0\n'
    )
    fn = _parse_function(source)
    # outer has 1 base + 1 If = 2 (inner's two If branches are excluded)
    assert _cyclomatic_complexity(fn) == 2


def test_complexity_decorators_do_not_affect_score():
    source = '@staticmethod\n@some_decorator\ndef f():\n    return 1\n'
    fn = _parse_function(source)
    assert _cyclomatic_complexity(fn) == 1


# ---------------------------------------------------------------------------
# _analyze_file
# ---------------------------------------------------------------------------


def test_analyze_file_reports_methods_with_class_qualname(tmp_path):
    src = (
        'class MyClass:\n'
        '    def method(self, x):\n'
        '        if x:\n'
        '            return 1\n'
        '        return 0\n'
    )
    path = tmp_path / 'sample.py'
    path.write_text(src)

    entries = _analyze_file(str(path), str(tmp_path))

    assert len(entries) == 1
    complexity, _relpath, lineno, qualname = entries[0]
    assert qualname == 'MyClass.method'
    assert complexity == 2
    assert lineno == 2


def test_analyze_file_reports_nested_function_with_locals_marker(tmp_path):
    src = (
        'def outer(x):\n'
        '    def inner(y):\n'
        '        if y:\n'
        '            return 1\n'
        '    if x:\n'
        '        return inner(x)\n'
    )
    path = tmp_path / 'nested.py'
    path.write_text(src)

    entries = _analyze_file(str(path), str(tmp_path))
    qualnames = {e[3] for e in entries}
    assert qualnames == {'outer', 'outer.<locals>.inner'}


def test_analyze_file_handles_syntax_error(tmp_path, capsys):
    path = tmp_path / 'broken.py'
    path.write_text('def f(:\n    pass\n')

    entries = _analyze_file(str(path), str(tmp_path))

    assert entries == []
    captured = capsys.readouterr()
    assert 'SyntaxError' in captured.err
    assert 'broken.py' in captured.err


def test_analyze_file_uses_relative_paths(tmp_path):
    sub = tmp_path / 'pkg'
    sub.mkdir()
    path = sub / 'mod.py'
    path.write_text('def f():\n    return 1\n')

    entries = _analyze_file(str(path), str(tmp_path))

    assert entries
    relpath = entries[0][1]
    # Portable across Windows/Linux: path separator may differ, but
    # os.path.relpath uses os.sep. Just assert the components are right.
    assert relpath.endswith('mod.py')
    assert 'pkg' in relpath


# ---------------------------------------------------------------------------
# _iter_python_files
# ---------------------------------------------------------------------------


def test_iter_python_files_prunes_pycache_and_dot_dirs(tmp_path):
    (tmp_path / 'keep.py').write_text('')
    (tmp_path / 'notpy.txt').write_text('')
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'skip.py').write_text('')
    (tmp_path / '.hidden').mkdir()
    (tmp_path / '.hidden' / 'skip.py').write_text('')

    found = sorted(os.path.basename(p) for p in _iter_python_files([str(tmp_path)]))
    assert found == ['keep.py']


def test_iter_python_files_accepts_single_file_root(tmp_path):
    path = tmp_path / 'only.py'
    path.write_text('')
    (tmp_path / 'other.txt').write_text('')

    found = list(_iter_python_files([str(path), str(tmp_path / 'other.txt')]))
    assert found == [str(path)]


# ---------------------------------------------------------------------------
# complexity_report (CLI entry point)
# ---------------------------------------------------------------------------


def test_complexity_report_honours_top_flag(capsys):
    complexity_report(argparse.Namespace(top=3))
    out = capsys.readouterr().out

    header_match = re.search(r'Cyclomatic complexity report \(top (\d+) of (\d+) functions\)', out)
    assert header_match is not None, out
    shown, total = int(header_match.group(1)), int(header_match.group(2))
    assert shown == 3
    assert total >= shown

    # Exactly three data rows between the two dashed separators
    lines = out.splitlines()
    sep_indices = [i for i, line in enumerate(lines) if line.startswith('---')]
    assert len(sep_indices) >= 2
    body = lines[sep_indices[0] + 1 : sep_indices[1]]
    assert len(body) == 3
    row_re = re.compile(r'^\s*\d+\s+\S+\.py:\d+\s+\S')
    for line in body:
        assert row_re.match(line), f'unexpected row format: {line!r}'

    # Summary line present
    assert 'Scanned' in out
    assert 'max=' in out
    assert 'mean=' in out


def test_complexity_report_rejects_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        complexity_report(argparse.Namespace(top=0))
    assert exc.value.code == 2
    assert 'ERROR' in capsys.readouterr().err


def test_complexity_report_rejects_negative():
    with pytest.raises(SystemExit) as exc:
        complexity_report(argparse.Namespace(top=-5))
    assert exc.value.code == 2
