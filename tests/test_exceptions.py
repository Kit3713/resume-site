"""Tests for the domain exception hierarchy (app/exceptions.py).

These tests lock in the inheritance contract: callers currently catching
`ValueError`, `LookupError`, or `KeyError` must keep working after we
migrate service `raise` sites to the domain-specific types.
"""

import pytest

from app.exceptions import DomainError, DuplicateError, NotFoundError, ValidationError


class TestDomainErrorBase:
    def test_is_an_exception(self):
        assert issubclass(DomainError, Exception)

    def test_carries_message(self):
        err = DomainError('oh no')
        assert str(err) == 'oh no'


class TestValidationError:
    def test_inherits_from_domain_error(self):
        assert issubclass(ValidationError, DomainError)

    def test_inherits_from_value_error(self):
        """Preserves `except ValueError:` in pre-existing callers."""
        assert issubclass(ValidationError, ValueError)

    def test_caught_as_value_error(self):
        with pytest.raises(ValueError, match='bad input'):
            raise ValidationError('bad input')

    def test_caught_as_domain_error(self):
        with pytest.raises(DomainError):
            raise ValidationError('bad input')


class TestNotFoundError:
    def test_inherits_from_domain_error(self):
        assert issubclass(NotFoundError, DomainError)

    def test_inherits_from_lookup_error(self):
        """Preserves `except LookupError:` and `except KeyError:`
        handlers — KeyError also inherits from LookupError so catchers
        of the base type keep working."""
        assert issubclass(NotFoundError, LookupError)

    def test_caught_as_lookup_error(self):
        with pytest.raises(LookupError):
            raise NotFoundError('missing')


class TestDuplicateError:
    def test_inherits_from_domain_error(self):
        assert issubclass(DuplicateError, DomainError)

    def test_inherits_from_value_error(self):
        assert issubclass(DuplicateError, ValueError)

    def test_carries_conflicting_value(self):
        err = DuplicateError('slug collision', conflicting_value='my-post')
        assert err.conflicting_value == 'my-post'
        assert 'slug collision' in str(err)

    def test_conflicting_value_defaults_to_none(self):
        err = DuplicateError('no extra info')
        assert err.conflicting_value is None


class TestServiceIntegration:
    """Smoke-test that services actually raise the new types."""

    def test_service_items_raises_validation_error(self):
        from app.services.service_items import add_service

        with pytest.raises(ValidationError, match='Service title cannot be empty'):
            add_service(db=None, title='')

    def test_stats_raises_validation_error(self):
        from app.services.stats import add_stat

        with pytest.raises(ValidationError, match='Stat label cannot be empty'):
            add_stat(db=None, label='', value=0)

    def test_reviews_raises_validation_error(self):
        from app.services.reviews import get_reviews_by_status

        with pytest.raises(ValidationError, match='Invalid review status'):
            get_reviews_by_status(db=None, status='bogus')

    def test_settings_unknown_key_raises_not_found_error(self, app):
        from app.db import get_db
        from app.services.settings_svc import set_one

        with app.app_context(), pytest.raises(NotFoundError, match='Unknown setting key'):
            set_one(get_db(), 'this_key_does_not_exist', 'x')

    def test_settings_unknown_key_still_caught_by_lookup_error(self, app):
        """LookupError is the stdlib superclass of KeyError — this
        preserves any future `except KeyError:` handler that might come
        back if we expand the registry API surface."""
        from app.db import get_db
        from app.services.settings_svc import set_one

        with app.app_context(), pytest.raises(LookupError):
            set_one(get_db(), 'this_key_does_not_exist', 'x')
