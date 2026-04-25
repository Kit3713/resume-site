"""Form-field extraction helpers.

Phase 29.1: deduplicate the request.form.get(...).strip() idiom that
was repeated across eight route modules.
"""


def get_stripped(form, key: str, default: str = '') -> str:
    """Return form[key] with surrounding whitespace stripped, or default if absent.

    Behaviour is byte-identical to ``request.form.get(key, default).strip()``.
    Only ``str.strip()`` — no case folding, no normalisation.
    """
    value = form.get(key, default)
    return value.strip() if value else default
