"""Turns exceptions into clean JSON responses.

No handler ever includes exception text or a traceback in the body. Details
go to the server log alongside the request id, which is also returned to the
client so a support conversation can find the matching log line.
"""

import logging
import math
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import messages
from app.errors import (
    AccountInactive,
    AuthError,
    AuthUnavailable,
    DifferentDocument,
    IngestionFailed,
    PermissionDenied,
    PolicyFormatError,
    PolicyNotYetEffective,
    TooManyAttempts,
    UnknownCompany,
    UnknownEmployee,
    VersionConflict,
)

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_body(request: Request, status: str, message: str) -> dict:
    return {"status": status, "message": message, "request_id": request_id(request)}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        try:
            response = await call_next(request)
        except Exception:
            # Raised past every handler; still answer in our own format.
            log.exception("unhandled error request_id=%s", request.state.request_id)
            response = JSONResponse(error_body(request, "error", messages.UNEXPECTED), status_code=500)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(AuthError)
    async def on_auth_error(request: Request, exc: AuthError):
        log.info("auth rejected request_id=%s reason=%s", request_id(request), exc)
        return JSONResponse(
            error_body(request, "unauthorized", messages.SESSION_INVALID),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthUnavailable)
    async def on_auth_unavailable(request: Request, exc: AuthUnavailable):
        log.exception("token store unavailable request_id=%s", request_id(request))
        return JSONResponse(
            error_body(request, "auth_unavailable", messages.SIGN_IN_UNAVAILABLE),
            status_code=503,
            headers={"Retry-After": "30"},
        )

    # Our client list is confidential: an unknown company and an unknown
    # employee get the same reply, and only the log says which it was.
    @app.exception_handler(UnknownCompany)
    @app.exception_handler(UnknownEmployee)
    async def on_unknown_account(request: Request, exc: UnknownCompany | UnknownEmployee):
        log.info("sign-in refused request_id=%s reason=%s", request_id(request), type(exc).__name__)
        return JSONResponse(error_body(request, "unknown_account", messages.UNKNOWN_ACCOUNT), status_code=401)

    @app.exception_handler(AccountInactive)
    async def on_account_inactive(request: Request, exc: AccountInactive):
        message = messages.ACCOUNT_INACTIVE.format(company=str(exc))
        return JSONResponse(error_body(request, "account_inactive", message), status_code=403)

    @app.exception_handler(TooManyAttempts)
    async def on_too_many_attempts(request: Request, exc: TooManyAttempts):
        minutes = math.ceil(exc.retry_after_s / 60)
        wait = "a minute" if minutes <= 1 else f"{minutes} minutes"
        log.warning("login rate limited request_id=%s", request_id(request))
        return JSONResponse(
            error_body(request, "too_many_attempts", messages.TOO_MANY_ATTEMPTS.format(wait=wait)),
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    @app.exception_handler(PermissionDenied)
    async def on_permission_denied(request: Request, exc: PermissionDenied):
        log.info("permission denied request_id=%s reason=%s", request_id(request), exc)
        return JSONResponse(error_body(request, "forbidden", messages.NOT_ALLOWED), status_code=403)

    # Policy errors are shown to the uploading admin. Their text is written
    # by our own parser and registry, never copied from an exception chain.
    @app.exception_handler(PolicyFormatError)
    async def on_policy_format(request: Request, exc: PolicyFormatError):
        message = messages.POLICY_UNREADABLE.format(reason=str(exc))
        return JSONResponse(error_body(request, "invalid_policy", message), status_code=422)

    @app.exception_handler(VersionConflict)
    async def on_version_conflict(request: Request, exc: VersionConflict):
        message = messages.POLICY_VERSION_CONFLICT.format(reason=str(exc).capitalize())
        return JSONResponse(error_body(request, "version_conflict", message), status_code=409)

    @app.exception_handler(DifferentDocument)
    async def on_different_document(request: Request, exc: DifferentDocument):
        message = messages.POLICY_DIFFERENT_DOCUMENT.format(title=exc.live_title, doc_id=exc.live_doc_id)
        return JSONResponse(error_body(request, "different_document", message), status_code=409)

    @app.exception_handler(PolicyNotYetEffective)
    async def on_not_yet_effective(request: Request, exc: PolicyNotYetEffective):
        current = f"version {exc.live_version}" if exc.live_version is not None else "no policy"
        message = messages.POLICY_NOT_YET_EFFECTIVE.format(
            date=f"{exc.effective_from.day} {exc.effective_from:%B %Y}", current=current
        )
        return JSONResponse(error_body(request, "not_yet_effective", message), status_code=409)

    @app.exception_handler(IngestionFailed)
    async def on_ingestion_failed(request: Request, exc: IngestionFailed):
        return JSONResponse(error_body(request, "ingestion_failed", messages.POLICY_UPDATE_FAILED), status_code=503)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # Field names and reasons are safe to show and help the caller fix
        # the request; the raw input values are not echoed back.
        problems = [{"field": ".".join(str(p) for p in err["loc"][1:]), "problem": err["msg"]} for err in exc.errors()]
        body = error_body(request, "invalid_request", messages.BAD_REQUEST)
        body["problems"] = problems
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        log.exception("unexpected error request_id=%s", request_id(request))
        return JSONResponse(error_body(request, "error", messages.UNEXPECTED), status_code=500)
