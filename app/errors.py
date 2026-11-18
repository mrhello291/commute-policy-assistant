"""Domain exceptions.

Code raises these instead of HTTP errors so the business logic stays
independent of FastAPI. The API layer maps each one to a status code and a
plain user-facing message (see `app.api.error_handlers`).
"""


class AppError(Exception):
    """Base class for every error the application raises on purpose."""


class AuthError(AppError):
    """The caller could not be authenticated (missing, expired or forged token)."""


class AuthUnavailable(AppError):
    """Sign-in state could not be checked (the token store is unreachable).

    Requests fail closed: the caller is told to try again, never let through.
    """


class PermissionDenied(AppError):
    """The caller is authenticated but not allowed to do this."""


class PolicyFormatError(AppError):
    """A policy document could not be parsed. Nothing has been stored."""


class VersionConflict(AppError):
    """A policy version can't be ingested in the registry's current state."""


class IngestionFailed(AppError):
    """Ingesting a policy failed part-way. The previously live version is unchanged."""


class TenantIsolationError(AppError):
    """Retrieval returned text from a company other than the caller's.

    This should be impossible. If it ever happens the request fails closed
    rather than risk showing one company's policy to another's employee.
    """
