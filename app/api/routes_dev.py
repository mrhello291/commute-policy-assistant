"""Mock identity provider, mounted only when APP_ENV=dev.

In production the company's own IdP would issue these tokens. Here it lets the
demo page and the evaluation runner act as any employee of any company.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container
from app.auth import Role
from app.container import Container

router = APIRouter(prefix="/dev", tags=["dev"])


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=64)
    company_id: str = Field(min_length=1, max_length=64)
    role: Role = Role.EMPLOYEE


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=TokenResponse)
def issue_token(body: TokenRequest, container: Container = Depends(get_container)) -> TokenResponse:
    token = container.token_issuer.issue(body.subject, body.company_id, body.role).token
    return TokenResponse(access_token=token)
