"""Policy administration: upload a new version, see which one is live.

The company is always the caller's own, taken from their token. A document
that doesn't name its company is filed under the caller's; one whose
document-control block names another company is refused.
"""

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from app.api.deps import get_container, get_principal, require_role
from app.auth import Principal, Role
from app.container import Container
from app.errors import PolicyFormatError
from app.policies.pdf_parser import MAX_PDF_BYTES

router = APIRouter(prefix="/v1/policies", tags=["policies"])


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
    file: UploadFile = File(description="The policy as a PDF. Markdown with front matter is also accepted."),
    principal: Principal = Depends(require_role(Role.POLICY_ADMIN)),
    container: Container = Depends(get_container),
) -> UploadResult:
    # The format is read from the content, not the file name or content type.
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise PolicyFormatError(f"the file is larger than {MAX_PDF_BYTES // 1_000_000} MB")
    company = container.auth.directory.company(principal.company_id)
    report = container.ingestion.ingest(
        data,
        acting_company=principal.company_id,
        acting_company_name=company.company_name if company else None,
    )
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
