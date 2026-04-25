"""Tests for the shared form-field helper (app/services/form.py).

Behaviour MUST be byte-identical to the pre-29.1 idiom across the route
modules, so each case below pairs the helper output against the
literal expression it replaced. If a future refactor diverges from
``str.strip()`` semantics, every admin/blog/API form caller silently
shifts. Pin the contract.
"""

from app.services.form import get_stripped


class TestGetStrippedHappyPath:
    def test_returns_stripped_value_when_key_present(self):
        assert get_stripped({'name': '  alice  '}, 'name') == 'alice'

    def test_returns_value_unchanged_when_no_surrounding_whitespace(self):
        assert get_stripped({'name': 'alice'}, 'name') == 'alice'

    def test_strips_only_surrounding_whitespace_internal_preserved(self):
        assert get_stripped({'name': '  hello world  '}, 'name') == 'hello world'


class TestGetStrippedDefaults:
    def test_returns_default_when_key_absent(self):
        assert get_stripped({}, 'missing', 'fallback') == 'fallback'

    def test_default_is_empty_string_when_not_specified(self):
        assert get_stripped({}, 'missing') == ''

    def test_returns_default_when_value_is_empty_string(self):
        # Matches ``request.form.get('x', 'grid').strip()``: when the
        # form sent an empty string, dict.get returns it (truthy check
        # is False), and the helper returns the default. This is the
        # display_tier='grid' contract from app/routes/api.py.
        assert get_stripped({'tier': ''}, 'tier', 'grid') == 'grid'


class TestGetStrippedWhitespaceOnlyInput:
    """Whitespace-only input is the subtle case. The original idiom

        request.form.get('x', '').strip()

    returns ``''`` on whitespace-only input — `.strip()` reduces it.
    The helper must match that, NOT fall through to the default.
    """

    def test_whitespace_only_returns_empty_string_not_default(self):
        # Per pre-29.1 byte-for-byte behaviour: '   '.strip() is '',
        # and that's what the call sites observed. The default is
        # only returned when the key is absent OR the value is falsy
        # (empty string / None). A whitespace string is truthy, so it
        # follows the strip branch.
        assert get_stripped({'name': '   '}, 'name', 'fallback') == ''

    def test_whitespace_only_returns_empty_string_default(self):
        assert get_stripped({'name': '   '}, 'name') == ''


class TestGetStrippedWhitespaceCharacters:
    """``str.strip()`` with no argument removes all whitespace
    characters by default — space, tab, newline, carriage return,
    form feed, vertical tab. Pin each one so a future helper
    rewrite can't accidentally narrow the set."""

    def test_strips_tab(self):
        assert get_stripped({'x': '\thello\t'}, 'x') == 'hello'

    def test_strips_newline(self):
        assert get_stripped({'x': '\nhello\n'}, 'x') == 'hello'

    def test_strips_carriage_return(self):
        assert get_stripped({'x': '\rhello\r'}, 'x') == 'hello'

    def test_strips_mixed_whitespace(self):
        assert get_stripped({'x': '\t \r\n hello \n\r \t'}, 'x') == 'hello'


class TestGetStrippedByteIdentical:
    """Cross-check the helper against the two literal idioms it
    replaces in the codebase. Any divergence here means the refactor
    is no longer a pure no-op."""

    @staticmethod
    def _legacy_with_default(form, key, default=''):
        # Pattern from contact.py / blog_admin.py / review.py / admin.py:
        #     request.form.get('field', '').strip()
        return form.get(key, default).strip()

    @staticmethod
    def _legacy_or_idiom(form, key, default=''):
        # Pattern from api.py / admin.py webhooks/api-tokens:
        #     (request.form.get('field') or '').strip()
        return (form.get(key) or default).strip()

    def test_helper_matches_default_idiom_present(self):
        form = {'name': '  alice  '}
        assert get_stripped(form, 'name') == self._legacy_with_default(form, 'name')

    def test_helper_matches_default_idiom_absent(self):
        form = {}
        assert get_stripped(form, 'name') == self._legacy_with_default(form, 'name')

    def test_helper_matches_default_idiom_whitespace(self):
        form = {'name': '   '}
        assert get_stripped(form, 'name') == self._legacy_with_default(form, 'name')

    def test_helper_matches_or_idiom_present(self):
        form = {'name': '  alice  '}
        assert get_stripped(form, 'name') == self._legacy_or_idiom(form, 'name')

    def test_helper_matches_or_idiom_absent(self):
        form = {}
        assert get_stripped(form, 'name') == self._legacy_or_idiom(form, 'name')

    def test_helper_matches_or_idiom_with_non_empty_default(self):
        # display_tier='grid' parity check (app/routes/api.py:1235).
        form = {'tier': '  list  '}
        assert get_stripped(form, 'tier', 'grid') == self._legacy_or_idiom(form, 'tier', 'grid')

    def test_helper_matches_or_idiom_with_default_on_absent(self):
        form = {}
        assert get_stripped(form, 'tier', 'grid') == self._legacy_or_idiom(form, 'tier', 'grid')
