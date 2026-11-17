"""Issuing and verifying JWTs.

`TokenIssuer` plays the part of the company's identity provider (the brief asks
us to build a mock one). `TokenVerifier` is what the API trusts. Both use HS256
with a shared secret, which is enough for a single service; a real deployment
would verify an IdP's RS256 signature against its published keys instead.
"""

from datetime import UTC, datetime, timedelta

import jwt

from app.auth.principal import Principal, Role
from app.errors import AuthError

_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "company_id", "role"]


class TokenIssuer:
    def __init__(self, secret: str, issuer: str, audience: str, ttl: timedelta):
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl

    def issue(self, subject: str, company_id: str, role: Role = Role.EMPLOYEE) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": subject,
            "company_id": company_id,
            "role": role.value,
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "exp": now + self._ttl,
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM)


class TokenVerifier:
    def __init__(self, secret: str, issuer: str, audience: str):
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> Principal:
        """Return the caller encoded in `token`, or raise `AuthError`.

        The algorithm is pinned, so tokens signed with `none` or another
        algorithm are rejected rather than trusted.
        """
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

        subject = claims["sub"]
        company_id = claims["company_id"]
        if not isinstance(subject, str) or not subject:
            raise AuthError("invalid token: empty subject")
        if not isinstance(company_id, str) or not company_id:
            raise AuthError("invalid token: empty company_id")
        try:
            role = Role(claims["role"])
        except ValueError as exc:
            raise AuthError("invalid token: unknown role") from exc

        return Principal(subject=subject, company_id=company_id, role=role)
