"""Policy administration: upload a new version, see which one is live.

The company is always the caller's own, taken from their token. An admin
uploading a document whose front matter names another company is refused.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container, get_principal, require_role
from app.auth import Principal, Role
from app.container import Container
from app.policies.parser import MAX_DOCUMENT_BYTES

router = APIRouter(prefix="/v1/policies", tags=["policies"])


class PolicyUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES, description="The policy as Markdown")


class UploadResult(BaseModel):
    company_id: str
    version: int
    activated: bool
    chunk_count: int
    previous_version: int | None
    flagged_sections: list[str]
    note: str


class ActivePolicy(BaseModel):
    company_id: str
    company_name: str | None
    title: str | None
    version: int | None


@router.post("", response_model=UploadResult)
def upload_policy(
    body: PolicyUpload,
    principal: Principal = Depends(require_role(Role.POLICY_ADMIN)),
    container: Container = Depends(get_container),
) -> UploadResult:
    report = container.ingestion.ingest(body.content, acting_company=principal.company_id)
    return UploadResult(
        company_id=report.company_id,
        version=report.version,
        activated=report.activated,
        chunk_count=report.chunk_count,
        previous_version=report.previous_version,
        flagged_sections=list(report.flagged_sections),
        note=report.note,
    )


@router.get("/active", response_model=ActivePolicy)
def active_policy(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> ActivePolicy:
    live = container.registry.active(principal.company_id)
    return ActivePolicy(
        company_id=principal.company_id,
        company_name=live.company_name if live else None,
        title=live.title if live else None,
        version=live.version if live else None,
    )
