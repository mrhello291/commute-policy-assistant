import json
from pathlib import Path

import pytest

from app.auth import Role
from app.auth.directory import DirectoryError, EmployeeDirectory

DIRECTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "employees.json"


def _raw(*employees, company_id="acme"):
    return {"companies": [{"company_id": company_id, "company_name": "Acme", "employees": list(employees)}]}


def test_bundled_directory_loads_with_one_admin_per_company():
    directory = EmployeeDirectory.load(DIRECTORY_FILE)
    companies = {c.company_id for c in directory.companies()}
    assert companies == {"yamuna", "gomti", "medhavi"}
    for company_id in companies:
        staff = [e for e in directory.employees() if e.company_id == company_id]
        assert 3 <= len(staff) <= 4
        assert sum(e.role is Role.POLICY_ADMIN for e in staff) == 1


def test_member_only_matches_own_company():
    directory = EmployeeDirectory.load(DIRECTORY_FILE)
    assert directory.member("yamuna", "YAM-1001").name == "Ananya Rao"
    assert directory.member("gomti", "YAM-1001") is None
    assert directory.member("yamuna", "NOPE-1") is None


def test_inactive_flag_is_read():
    directory = EmployeeDirectory.load(DIRECTORY_FILE)
    assert directory.employee("GOM-2003").active is False
    assert directory.employee("GOM-2001").active is True


def test_duplicate_employee_id_across_companies_is_refused():
    raw = _raw({"employee_id": "E-1", "name": "A"})
    other = _raw({"employee_id": "E-1", "name": "B"}, company_id="other")
    raw["companies"].extend(other["companies"])
    with pytest.raises(DirectoryError, match="more than once"):
        EmployeeDirectory.from_dict(raw)


@pytest.mark.parametrize(
    "person",
    [
        {"employee_id": "e-1 lower", "name": "A"},
        {"employee_id": "E-1", "name": "A", "role": "superuser"},
        {"employee_id": "E-1"},
    ],
)
def test_bad_entries_are_refused(person):
    with pytest.raises(DirectoryError):
        EmployeeDirectory.from_dict(_raw(person))


def test_missing_or_broken_file_is_refused(tmp_path):
    with pytest.raises(DirectoryError, match="not found"):
        EmployeeDirectory.load(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(DirectoryError, match="not valid JSON"):
        EmployeeDirectory.load(broken)


def test_empty_directory_is_refused():
    with pytest.raises(DirectoryError):
        EmployeeDirectory.from_dict(json.loads('{"companies": []}'))
