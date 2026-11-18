"""Sign-in and per-request authentication.

    login(company, employee id)
      -> too many failed sign-ins from this client?  yes: TooManyAttempts
      -> company known?                 no: UnknownCompany
      -> employee works there?          no: UnknownEmployee
      -> employee still active?         no: AccountInactive
      -> sign a fresh JWT and record it in the token store

    authenticate(token), on every API request
      -> signature, issuer, audience, expiry           (TokenVerifier)
      -> token recorded, not revoked, not expired      (TokenStore)
      -> employee still in the directory, still active, same company and role

Each login issues a new token rather than handing back an earlier one: see
the README section on sign-in for why.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.auth.directory import Company, Employee, EmployeeDirectory, normalise_company_id, normalise_employee_id
from app.auth.principal import Principal
from app.auth.rate_limit import LoginRateLimiter
from app.auth.token_store import TokenRecord, TokenStore
from app.auth.tokens import TokenIssuer, TokenVerifier
from app.errors import AccountInactive, AuthError, LoginRefused, UnknownCompany, UnknownEmployee

log = logging.getLogger(__name__)

MAX_USER_AGENT_CHARS = 200


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated request: who is calling, and with which token."""

    principal: Principal
    jti: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    jti: str
    expires_at: datetime
    employee: Employee
    company: Company


class AuthService:
    def __init__(
        self,
        directory: EmployeeDirectory,
        store: TokenStore,
        issuer: TokenIssuer,
        verifier: TokenVerifier,
        max_sessions_per_employee: int,
        rate_limiter: LoginRateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._directory = directory
        self._store = store
        self._issuer = issuer
        self._verifier = verifier
        self._max_sessions = max_sessions_per_employee
        self._rate_limiter = rate_limiter
        self._clock = clock

    def login(
        self, company_id: str, employee_id: str, client_ip: str | None = None, user_agent: str | None = None
    ) -> LoginResult:
        client = client_ip or "unknown"
        if self._rate_limiter:
            self._rate_limiter.check(client)
        try:
            company, employee = self._find_employee(company_id, employee_id)
        except LoginRefused:
            if self._rate_limiter:
                self._rate_limiter.record_failure(client)
            raise

        signed = self._issuer.issue(employee.employee_id, company.company_id, employee.role)
        revoked = self._store.record(
            TokenRecord(
                jti=signed.jti,
                employee_id=employee.employee_id,
                company_id=company.company_id,
                role=employee.role.value,
                issued_at=int(signed.issued_at.timestamp()),
                expires_at=int(signed.expires_at.timestamp()),
                issued_by=self._issuer.issuer,
                client_ip=client_ip,
                user_agent=(user_agent or "")[:MAX_USER_AGENT_CHARS] or None,
            ),
            max_live_per_employee=self._max_sessions,
        )
        log.info(
            "login employee=%s company=%s role=%s jti=%s ended_old_sessions=%d",
            employee.employee_id,
            company.company_id,
            employee.role,
            signed.jti[:8],
            len(revoked),
        )
        return LoginResult(signed.token, signed.jti, signed.expires_at, employee, company)

    def _find_employee(self, company_id: str, employee_id: str) -> tuple[Company, Employee]:
        company = self._directory.company(normalise_company_id(company_id))
        if company is None:
            raise UnknownCompany("unknown company")
        # One message whether the ID is unknown or belongs to another
        # company, so sign-in can't be used to find out who works where.
        employee = self._directory.member(company.company_id, normalise_employee_id(employee_id))
        if employee is None:
            raise UnknownEmployee(company.company_name)
        if not employee.active:
            raise AccountInactive(company.company_name)
        return company, employee

    def authenticate(self, token: str) -> Session:
        """The session behind `token`, or `AuthError`. `AuthUnavailable` if the store is down."""
        verified = self._verifier.verify(token)
        principal = verified.principal

        record = self._store.get(verified.jti)
        if record is None:
            raise AuthError("token was not issued by this service, or its record was removed")
        if record.revoked_at is not None:
            raise AuthError(f"token revoked ({record.revoked_reason})")
        if not record.is_live(int(self._clock())):
            raise AuthError("token expired")
        if (record.employee_id, record.company_id, record.role) != (
            principal.subject,
            principal.company_id,
            principal.role.value,
        ):
            raise AuthError("token claims do not match the issued record")

        # The directory may have changed since sign-in (someone left, or
        # lost the admin role). The token then stops working straight away.
        employee = self._directory.member(principal.company_id, principal.subject)
        if employee is None or not employee.active or employee.role is not principal.role:
            raise AuthError("employee is no longer active in this role")

        return Session(principal=principal, jti=verified.jti, expires_at=verified.expires_at)

    def logout(self, session: Session) -> None:
        self._store.revoke(session.jti, "logout", int(self._clock()))
        log.info("logout employee=%s jti=%s", session.principal.subject, session.jti[:8])

    @property
    def directory(self) -> EmployeeDirectory:
        return self._directory
