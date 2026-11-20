"""Demo helpers, mounted only when APP_ENV=dev.

Lists the sample accounts so the sign-in page can offer them. A real
deployment would never publish its employee list, or its list of client
companies.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_container
from app.container import Container

router = APIRouter(prefix="/dev", tags=["dev"])


class SampleAccount(BaseModel):
    company_id: str
    company_name: str
    employee_id: str
    name: str
    role: str
    active: bool


@router.get("/accounts", response_model=list[SampleAccount])
def sample_accounts(container: Container = Depends(get_container)) -> list[SampleAccount]:
    directory = container.auth.directory
    names = {c.company_id: c.company_name for c in directory.companies()}
    return [
        SampleAccount(
            company_id=e.company_id,
            company_name=names.get(e.company_id, e.company_id),
            employee_id=e.employee_id,
            name=e.name,
            role=e.role.value,
            active=e.active,
        )
        for e in directory.employees()
    ]
