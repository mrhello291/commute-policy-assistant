"""FastAPI dependencies shared by the routes.

`get_session` is the only way a route learns who is calling. It reads the
bearer token from the Authorization header and nothing else: there is
deliberately no header, query parameter, body field or cookie that can name
a company or stand in for the token.
"""

from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import Principal, Role
from app.auth.service import Session
from app.container import Container
from app.errors import AuthError, PermissionDenied

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
) -> Session:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("missing bearer token")
    return container.auth.authenticate(credentials.credentials)


def get_principal(session: Session = Depends(get_session)) -> Principal:
    return session.principal


def require_role(role: Role) -> Callable[..., Principal]:
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role is not role:
            raise PermissionDenied(f"requires role {role.value}")
        return principal

    return dependency
