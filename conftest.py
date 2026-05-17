"""Root-level pytest conftest.

Only carries a mutmut 3.5.0 + Python 3.12+ compatibility shim today.
Normal pytest runs from the worktree root never reach this code path —
the ``_install_mutmut_shim`` guard checks ``MUTANT_UNDER_TEST`` (set by
mutmut when it invokes pytest inside ``mutants/``) and is a silent no-op
otherwise. This file is mirrored into ``mutants/conftest.py`` via
``[tool.mutmut] also_copy`` in ``pyproject.toml``.

Background — mutmut 3.5.0 generates a ``_mutmut_trampoline`` per
mutated function that calls
``from mutmut.__main__ import record_trampoline_hit``. Because mutmut
was launched via ``python -m mutmut``, ``mutmut.__main__`` is bound to
the ``__main__`` module slot; the trampoline's secondary import then
re-executes the file's top level, which calls
``multiprocessing.set_start_method('fork')`` — raising
``RuntimeError('context has already been set')``. The shim turns the
redundant call into a no-op, leaving the originally configured context
untouched.
"""

from __future__ import annotations

import os


def _install_mutmut_shim() -> None:
    """Patch ``multiprocessing.set_start_method`` to be idempotent.

    Only runs when pytest is invoked under mutmut (``MUTANT_UNDER_TEST``
    env var present). Outside that context the function returns early so
    normal pytest behaviour is preserved.
    """
    if 'MUTANT_UNDER_TEST' not in os.environ:
        return

    import multiprocessing

    if getattr(multiprocessing.set_start_method, '_mutmut_patched', False):
        return

    original = multiprocessing.set_start_method

    def _idempotent(method=None, force=False):
        try:
            return original(method, force=force)
        except RuntimeError as exc:
            if 'context has already been set' in str(exc):
                return None
            raise

    _idempotent._mutmut_patched = True  # type: ignore[attr-defined]
    multiprocessing.set_start_method = _idempotent


_install_mutmut_shim()
