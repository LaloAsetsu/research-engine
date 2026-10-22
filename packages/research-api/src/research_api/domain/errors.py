"""Domain errors. Each one names a failure a caller can act on; none knows about HTTP.

The HTTP layer maps them to a status and a problem document (``entrypoints/http/problems.py``).
``slug`` is the stable short name that ends up in the problem ``type``.
"""

from dataclasses import dataclass


class ResearchApiError(Exception):
    """Base of every error the application raises on purpose."""

    slug = "internal"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class UnauthenticatedError(ResearchApiError):
    """No session, an expired one, or a wrong login."""

    slug = "unauthenticated"


class ForbiddenError(ResearchApiError):
    """The caller is known but may not do this (role, owner, CSRF, frozen set)."""

    slug = "forbidden"


class NotFoundError(ResearchApiError):
    """The set, item, run, job, document or page does not exist."""

    slug = "not-found"


class ConflictError(ResearchApiError):
    """The request is fine but the state moved: a stale ``If-Match``, an ambiguous id."""

    slug = "conflict"


class TooLargeError(ResearchApiError):
    """A body or an input is over its cap."""

    slug = "too-large"


@dataclass(frozen=True)
class FieldError:
    """One reason an input was refused: where (a JSON pointer) and why."""

    path: str
    message: str


class InvalidInputError(ResearchApiError):
    """The input does not satisfy its schema; ``errors`` says where."""

    slug = "invalid-input"

    def __init__(self, detail: str = "", errors: tuple[FieldError, ...] = ()) -> None:
        super().__init__(detail)
        self.errors = errors


class RateLimitedError(ResearchApiError):
    """Too many attempts; try again after ``retry_after_s`` seconds."""

    slug = "rate-limited"

    def __init__(self, detail: str = "", *, retry_after_s: int) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class UpstreamError(ResearchApiError):
    """A model server answered with an error."""

    slug = "upstream-error"


class UnavailableError(ResearchApiError):
    """The service cannot do this yet: index not ready, model server down, warming up."""

    slug = "unavailable"

    def __init__(self, detail: str = "", *, retry_after_s: int | None = None) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s
