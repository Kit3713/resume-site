"""Domain exception hierarchy (app/exceptions.py).

These exceptions mark the boundary between *business-logic* failures —
invalid input a user supplied, a record that doesn't exist, a slug that
collides — and *programmer-error* failures that should surface as 500s.

Rules of use:

*   Services **raise** these for conditions callers can recover from
    (bad user input, missing record, duplicate key). Plain `ValueError` /
    `KeyError` / etc. are reserved for true bugs (a function called with
    a wrong type, an impossible state).
*   Routes may **catch** them to translate into flash messages and
    redirects (admin flows) or aborts (public flows). A route that doesn't
    catch will let Flask's default 500 handler fire — which is the right
    behavior when something unexpected happens.
*   Exception classes inherit from the stdlib types callers might already
    be catching (`ValueError`, `LookupError`) so existing `except ValueError`
    handlers keep working during the transition.

Keep this module dependency-free — it's imported from both the service
and route layers. Adding Flask imports here would invert the dependency.
"""


class DomainError(Exception):
    """Base for every application-defined, recoverable error.

    Catching `DomainError` in a route lets you handle all business-logic
    failures uniformly (e.g., log + flash + redirect) while letting
    unrelated bugs continue to surface as 500s.
    """


class ValidationError(DomainError, ValueError):
    """User-supplied input failed a domain rule.

    Inherits from both `DomainError` (for uniform domain handling) and
    `ValueError` (so pre-existing `except ValueError:` blocks in tests and
    callers keep working).
    """


class NotFoundError(DomainError, LookupError):
    """Requested record does not exist.

    Prefer returning `None` from getter functions that only find-or-don't,
    and raising this from mutators where "not found" is an exceptional
    condition (e.g., `update_post(post_id=<gone>)`).
    """


class DuplicateError(DomainError, ValueError):
    """An insert/update violated a uniqueness constraint.

    Carry the conflicting key on the exception so callers don't need to
    re-query to produce a useful error message.
    """

    def __init__(self, message: str, *, conflicting_value: object = None) -> None:
        """Store ``conflicting_value`` alongside the message for caller diagnostics."""
        super().__init__(message)
        self.conflicting_value = conflicting_value
