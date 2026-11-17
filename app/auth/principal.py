"""The authenticated caller.

A `Principal` is only ever built from a verified token. Nothing in a request
body, query string or model output can create or change one, which is what
keeps every company's data separate.
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    EMPLOYEE = "employee"
    POLICY_ADMIN = "policy_admin"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    company_id: str
    role: Role
