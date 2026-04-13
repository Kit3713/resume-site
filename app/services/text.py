"""Text utilities (app/services/text.py).

Small, dependency-free string helpers shared across the codebase. Keeping
these out of the per-feature service modules makes them easy to reuse and
easier to test in isolation.

Currently exposes:
    slugify(text)  — URL-safe slug from arbitrary text.

Not covered here:
    Content-block key normalization (see app/services/content.create_block)
    uses a different, intentionally weaker scheme (`lower().replace(' ', '_')`)
    because those slugs are internal keys, not public URLs. Do not conflate.
"""

import re


def slugify(text: str) -> str:
    """Convert arbitrary text into a URL-safe, dash-separated slug.

    The algorithm is deterministic and locale-agnostic:
        1. Lowercase and strip surrounding whitespace.
        2. Drop every character that is not a word char, whitespace, or `-`.
        3. Collapse runs of whitespace or underscores into a single `-`.
        4. Collapse runs of consecutive `-`.
        5. Strip any leading/trailing `-`.

    Empty-ish inputs (empty string, whitespace only, all-symbol input) return
    an empty string — callers must decide how to handle that (e.g., blog post
    creation falls back to a timestamp-based slug).

    Example:
        >>> slugify('Hello, World!')
        'hello-world'
        >>> slugify('  Python  &  Go  ')
        'python-go'
    """
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')
