"""Sign-in, sign-out and "who am I".

Login checks the employee against the directory and returns a fresh JWT.
The same token is also set as an HttpOnly cookie, which is used for one
thing only: deciding whether a browser may open the web pages (the demo page
and the API docs). The API itself never reads that cookie; every API call
needs the token in the Authorization header, so a cookie sent by a browser
can't be used to make a request on someone's behalf.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import messages
from app.api.deps import get_container, get_session
from app.auth.service import Session
from app.container import Container

router = APIRouter(prefix="/v1", tags=["auth"])

SESSION_COOKIE = "cpa_session"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(min_length=1, max_length=64, examples=["yamuna"])
    employee_id: str = Field(min_length=1, max_length=32, examples=["YAM-1001"])


class EmployeeOut(BaseModel):
    employee_id: str
    name: str
    role: str


class CompanyOut(BaseModel):
    company_id: str
    company_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    expires_in: int
    employee: EmployeeOut
    company: CompanyOut


class MeResponse(BaseModel):
    subject: str
    company_id: str
    role: str
    expires_at: datetime


class SignedOut(BaseModel):
    status: str = "signed_out"
    message: str = messages.SIGNED_OUT


@router.post("/auth/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> LoginResponse:
    result = container.auth.login(
        body.company_id,
        body.employee_id,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    expires_in = max(0, int((result.expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        result.token,
        max_age=expires_in,
        httponly=True,
        samesite="strict",
        secure=not container.settings.is_dev,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        access_token=result.token,
        expires_at=result.expires_at,
        expires_in=expires_in,
        employee=EmployeeOut(
            employee_id=result.employee.employee_id, name=result.employee.name, role=result.employee.role.value
        ),
        company=CompanyOut(company_id=result.company.company_id, company_name=result.company.company_name),
    )


@router.post("/auth/logout", response_model=SignedOut)
def logout(
    response: Response,
    session: Session = Depends(get_session),
    container: Container = Depends(get_container),
) -> SignedOut:
    container.auth.logout(session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return SignedOut()


@router.get("/me", response_model=MeResponse)
def whoami(session: Session = Depends(get_session)) -> MeResponse:
    p = session.principal
    return MeResponse(subject=p.subject, company_id=p.company_id, role=p.role.value, expires_at=session.expires_at)
