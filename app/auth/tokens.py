"""Signing and checking JWTs.

`TokenIssuer` plays the part of the company's identity provider (the brief asks
us to build a mock one). `TokenVerifier` checks the signature and claims. Both
use HS256 with a shared secret, which is enough for a single service; a real
deployment would verify an IdP's RS256 signature against its published keys.

Every token carries a unique `jti`. The signature only proves the token was
issued by us; whether it is still usable (not logged out, employee still
active) is decided by `AuthService` against the token store.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.auth.principal import Principal, Role
from app.errors import AuthError

_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "jti", "company_id", "role"]


@dataclass(frozen=True, slots=True)
class SignedToken:
    token: str
    jti: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    principal: Principal
    jti: str
    expires_at: datetime


class TokenIssuer:
    def __init__(self, secret: str, issuer: str, audience: str, ttl: timedelta):
        self._secret = secret
        self.issuer = issuer
        self._audience = audience
        self._ttl = ttl

    def issue(self, subject: str, company_id: str, role: Role = Role.EMPLOYEE) -> SignedToken:
        # Whole seconds, so the stored times match the token's claims exactly.
        now = datetime.now(UTC).replace(microsecond=0)
        jti = uuid.uuid4().hex
        claims = {
            "sub": subject,
            "company_id": company_id,
            "role": role.value,
            "jti": jti,
            "iss": self.issuer,
            "aud": self._audience,
            "iat": now,
            "exp": now + self._ttl,
        }
        token = jwt.encode(claims, self._secret, algorithm=_ALGORITHM)
        return SignedToken(token=token, jti=jti, issued_at=now, expires_at=now + self._ttl)


class TokenVerifier:
    def __init__(self, secret: str, issuer: str, audience: str):
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> VerifiedToken:
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

        for name in ("sub", "company_id", "jti"):
            if not isinstance(claims[name], str) or not claims[name]:
                raise AuthError(f"invalid token: empty {name}")
        try:
            role = Role(claims["role"])
        except ValueError as exc:
            raise AuthError("invalid token: unknown role") from exc

        return VerifiedToken(
            principal=Principal(subject=claims["sub"], company_id=claims["company_id"], role=role),
            jti=claims["jti"],
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        )
