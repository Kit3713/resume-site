"""Tests for the shared text utilities (app/services/text.py).

`slugify` is load-bearing for blog URLs — a behavior change here silently
rewrites every auto-generated slug on the site. Pin the algorithm explicitly.
"""

import pytest

from app.services.text import slugify


class TestSlugifyHappyPath:
    def test_simple_text(self):
        assert slugify('Hello World') == 'hello-world'

    def test_lowercases(self):
        assert slugify('UPPER CASE') == 'upper-case'

    def test_strips_surrounding_whitespace(self):
        assert slugify('  spaced  ') == 'spaced'

    def test_collapses_internal_whitespace(self):
        assert slugify('many    spaces   here') == 'many-spaces-here'


class TestSlugifySpecialCharacters:
    def test_drops_punctuation(self):
        assert slugify('Hello, World!') == 'hello-world'

    def test_drops_emojis(self):
        assert slugify('Hello 🙂 World') == 'hello-world'

    def test_drops_quotes(self):
        assert slugify('"Quoted" Title') == 'quoted-title'

    def test_preserves_numbers(self):
        assert slugify('Python 3.12 release') == 'python-312-release'

    def test_converts_underscores_to_dashes(self):
        assert slugify('foo_bar_baz') == 'foo-bar-baz'

    def test_collapses_existing_dashes(self):
        assert slugify('foo---bar') == 'foo-bar'

    def test_strips_leading_and_trailing_dashes(self):
        assert slugify('---edges---') == 'edges'


class TestSlugifyEdgeCases:
    def test_empty_string(self):
        assert slugify('') == ''

    def test_whitespace_only(self):
        assert slugify('   ') == ''

    def test_symbols_only(self):
        assert slugify('!!!@@@###') == ''

    def test_single_dash(self):
        assert slugify('-') == ''

    def test_idempotent_on_already_slug(self):
        assert slugify('already-a-slug') == 'already-a-slug'


class TestSlugifyUnicode:
    """`\\w` in Python regex is unicode-aware by default; non-ASCII
    letters survive rather than being transliterated."""

    @pytest.mark.parametrize(
        ('text', 'expected'),
        [
            ('café au lait', 'café-au-lait'),
            ('naïve approach', 'naïve-approach'),
            ('日本語 blog post', '日本語-blog-post'),
        ],
    )
    def test_preserves_unicode_letters(self, text, expected):
        assert slugify(text) == expected
