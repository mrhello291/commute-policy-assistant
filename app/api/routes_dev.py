"""Demo helpers, mounted only when APP_ENV=dev.

Lists the sample accounts so the sign-in page can offer them. A real
deployment would never publish its employee list.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_container
from app.container import Container

router = APIRouter(prefix="/dev", tags=["dev"])


class SampleAccount(BaseModel):
    company_id: str
    employee_id: str
    name: str
    role: str
    active: bool


@router.get("/accounts", response_model=list[SampleAccount])
def sample_accounts(container: Container = Depends(get_container)) -> list[SampleAccount]:
    return [
        SampleAccount(
            company_id=e.company_id, employee_id=e.employee_id, name=e.name, role=e.role.value, active=e.active
        )
        for e in container.auth.directory.employees()
    ]
