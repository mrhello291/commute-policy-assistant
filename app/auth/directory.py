"""The mock employee directory.

Stands in for each client company's HR system: it says which employees exist,
which company each one works for, their role, and whether they still work
there. It is read from a JSON file and never written by the app.

A broken directory stops the service at startup rather than letting it run
with no one able to sign in, or with two companies claiming the same ID.
Edits made while it runs are picked up on the next request (`ReloadingDirectory`);
a broken edit is ignored and the last good version stays in use.
"""

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.auth.principal import Role

log = logging.getLogger(__name__)

# Employee IDs are matched case-insensitively and stored upper-case.
EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
COMPANY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class DirectoryError(Exception):
    """The directory file is missing or inconsistent."""


@dataclass(frozen=True, slots=True)
class Employee:
    employee_id: str
    name: str
    company_id: str
    role: Role
    active: bool = True


@dataclass(frozen=True, slots=True)
class Company:
    company_id: str
    company_name: str


def normalise_employee_id(raw: str) -> str:
    return raw.strip().upper()


def normalise_company_id(raw: str) -> str:
    return raw.strip().lower()


class EmployeeDirectory:
    def __init__(self, companies: list[Company], employees: list[Employee]):
        self._companies = {c.company_id: c for c in companies}
        self._employees = {e.employee_id: e for e in employees}

    @classmethod
    def load(cls, path: Path) -> "EmployeeDirectory":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DirectoryError(f"employee directory not found at {path}") from exc
        except (OSError, ValueError) as exc:
            raise DirectoryError(f"employee directory at {path} is not valid JSON: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "EmployeeDirectory":
        companies: list[Company] = []
        employees: list[Employee] = []
        seen_ids: set[str] = set()
        try:
            for entry in raw["companies"]:
                company_id = entry["company_id"]
                if not COMPANY_ID_PATTERN.match(company_id):
                    raise DirectoryError(f"invalid company_id {company_id!r}")
                if any(c.company_id == company_id for c in companies):
                    raise DirectoryError(f"company {company_id!r} is listed twice")
                companies.append(Company(company_id, entry["company_name"]))

                for person in entry["employees"]:
                    employee_id = person["employee_id"]
                    if not EMPLOYEE_ID_PATTERN.match(employee_id):
                        raise DirectoryError(f"invalid employee_id {employee_id!r}")
                    if employee_id in seen_ids:
                        raise DirectoryError(f"employee_id {employee_id!r} appears more than once")
                    seen_ids.add(employee_id)
                    try:
                        role = Role(person.get("role", Role.EMPLOYEE.value))
                    except ValueError as exc:
                        raise DirectoryError(f"unknown role for {employee_id}") from exc
                    employees.append(
                        Employee(
                            employee_id=employee_id,
                            name=person["name"],
                            company_id=company_id,
                            role=role,
                            active=bool(person.get("active", True)),
                        )
                    )
        except (KeyError, TypeError) as exc:
            raise DirectoryError(f"employee directory is missing a field: {exc}") from exc
        if not companies:
            raise DirectoryError("employee directory lists no companies")
        return cls(companies, employees)

    def company(self, company_id: str) -> Company | None:
        return self._companies.get(company_id)

    def companies(self) -> list[Company]:
        return sorted(self._companies.values(), key=lambda c: c.company_name)

    def employee(self, employee_id: str) -> Employee | None:
        return self._employees.get(employee_id)

    def employees(self) -> list[Employee]:
        return sorted(self._employees.values(), key=lambda e: e.employee_id)

    def member(self, company_id: str, employee_id: str) -> Employee | None:
        """The employee with this ID, but only if they belong to `company_id`."""
        person = self._employees.get(employee_id)
        return person if person and person.company_id == company_id else None


class ReloadingDirectory:
    """The directory file, re-read whenever it changes on disk.

    Each lookup costs one `stat` call. When the file's modification time,
    size or inode changes, it is loaded and validated again. If the new
    version is broken, the error is logged once and the previous version
    keeps serving, so a half-saved edit can't lock everyone out.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._signature = self._stat()
        self._current = EmployeeDirectory.load(path)  # a broken file fails startup

    def current(self) -> EmployeeDirectory:
        signature = self._stat()
        if signature is None or signature == self._signature:
            return self._current
        with self._lock:
            if signature != self._signature:
                self._signature = signature
                try:
                    self._current = EmployeeDirectory.load(self._path)
                    log.info("employee directory reloaded from %s", self._path)
                except DirectoryError:
                    log.exception("employee directory edit rejected, still using the previous version")
        return self._current

    def _stat(self) -> tuple[int, int, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            # Missing for a moment (an editor replacing the file): keep serving.
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    # The same lookups as EmployeeDirectory, always on the current version.

    def company(self, company_id: str) -> Company | None:
        return self.current().company(company_id)

    def companies(self) -> list[Company]:
        return self.current().companies()

    def employee(self, employee_id: str) -> Employee | None:
        return self.current().employee(employee_id)

    def employees(self) -> list[Employee]:
        return self.current().employees()

    def member(self, company_id: str, employee_id: str) -> Employee | None:
        return self.current().member(company_id, employee_id)
