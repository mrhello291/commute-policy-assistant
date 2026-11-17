"""Domain exceptions.

Code raises these instead of HTTP errors so the business logic stays
independent of FastAPI. The API layer maps each one to a status code and a
plain user-facing message (see `app.api.error_handlers`).
"""


class AppError(Exception):
    """Base class for every error the application raises on purpose."""


class AuthError(AppError):
    """The caller could not be authenticated (missing, expired or forged token)."""


class PermissionDenied(AppError):
    """The caller is authenticated but not allowed to do this."""


class PolicyFormatError(AppError):
    """A policy document could not be parsed. Nothing has been stored."""
