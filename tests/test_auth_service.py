import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from app.auth import Principal, Role, TokenIssuer, TokenVerifier
from app.auth.directory import EmployeeDirectory
from app.auth.service import AuthService
from app.auth.token_store import TokenStore
from app.errors import AccountInactive, AuthError, UnknownCompany, UnknownEmployee

DIRECTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "employees.json"
SECRET = "test-secret-that-is-at-least-32-bytes-long"


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


def _directory(**overrides):
    """The bundled directory, with optional per-employee field overrides."""
    raw = json.loads(DIRECTORY_FILE.read_text())
    for company in raw["companies"]:
        for person in company["employees"]:
            person.update(overrides.get(person["employee_id"], {}))
    return EmployeeDirectory.from_dict(raw)


def _service(tmp_path, directory=None, clock=None, max_sessions=5):
    return AuthService(
        directory or _directory(),
        TokenStore(tmp_path / "auth.db"),
        TokenIssuer(SECRET, "idp", "api", ttl=timedelta(minutes=30)),
        TokenVerifier(SECRET, "idp", "api"),
        max_sessions_per_employee=max_sessions,
        clock=clock or Clock(),
    )


@pytest.fixture
def auth(tmp_path):
    return _service(tmp_path)


def test_login_issues_a_token_for_the_directory_role(auth):
    result = auth.login("yamuna", "YAM-1004", client_ip="10.0.0.1", user_agent="curl/8")
    assert result.employee.name == "Vikram Shetty"
    assert result.company.company_name == "Yamuna Software Services"
    session = auth.authenticate(result.token)
    assert session.principal == Principal("YAM-1004", "yamuna", Role.POLICY_ADMIN)
    assert session.jti == result.jti


def test_login_ignores_case_and_whitespace(auth):
    result = auth.login("  Yamuna ", " yam-1001 ")
    assert result.employee.employee_id == "YAM-1001"


def test_unknown_company_is_refused(auth):
    with pytest.raises(UnknownCompany):
        auth.login("nosuchco", "YAM-1001")


def test_employee_of_another_company_gets_the_same_refusal_as_an_unknown_id(auth):
    with pytest.raises(UnknownEmployee) as wrong_company:
        auth.login("gomti", "YAM-1001")
    with pytest.raises(UnknownEmployee) as unknown:
        auth.login("gomti", "GOM-9999")
    assert str(wrong_company.value) == str(unknown.value) == "Gomti Logistics"


def test_inactive_employee_cannot_sign_in(auth):
    with pytest.raises(AccountInactive):
        auth.login("gomti", "GOM-2003")


def test_each_login_gets_a_new_token(auth):
    first = auth.login("yamuna", "YAM-1001")
    second = auth.login("yamuna", "YAM-1001")
    assert first.jti != second.jti
    auth.authenticate(first.token)
    auth.authenticate(second.token)


def test_logout_ends_only_that_session(auth):
    phone = auth.login("yamuna", "YAM-1001")
    laptop = auth.login("yamuna", "YAM-1001")
    auth.logout(auth.authenticate(phone.token))
    with pytest.raises(AuthError, match="revoked"):
        auth.authenticate(phone.token)
    auth.authenticate(laptop.token)


def test_oldest_session_ends_beyond_the_limit(tmp_path):
    auth = _service(tmp_path, max_sessions=2)
    tokens = [auth.login("yamuna", "YAM-1001").token for _ in range(3)]
    with pytest.raises(AuthError, match="session_limit"):
        auth.authenticate(tokens[0])
    for token in tokens[1:]:
        auth.authenticate(token)


def test_sessions_survive_a_restart(tmp_path):
    token = _service(tmp_path).login("yamuna", "YAM-1001").token
    assert _service(tmp_path).authenticate(token).principal.subject == "YAM-1001"


def test_validly_signed_token_without_a_record_is_refused(tmp_path):
    other_instance = _service(tmp_path / "elsewhere")
    token = other_instance.login("yamuna", "YAM-1001").token
    with pytest.raises(AuthError, match="not issued"):
        _service(tmp_path).authenticate(token)


def test_expiry_is_checked_against_the_record_too(tmp_path):
    clock = Clock()
    auth = _service(tmp_path, clock=clock)
    token = auth.login("yamuna", "YAM-1001").token
    clock.offset = 31 * 60
    with pytest.raises(AuthError, match="expired"):
        auth.authenticate(token)


def test_token_stops_working_when_employee_is_deactivated(tmp_path):
    token = _service(tmp_path).login("yamuna", "YAM-1001").token
    after_hr_update = _service(tmp_path, directory=_directory(**{"YAM-1001": {"active": False}}))
    with pytest.raises(AuthError, match="no longer active"):
        after_hr_update.authenticate(token)


def test_token_stops_working_when_admin_role_is_removed(tmp_path):
    token = _service(tmp_path).login("yamuna", "YAM-1004").token
    demoted = _service(tmp_path, directory=_directory(**{"YAM-1004": {"role": "employee"}}))
    with pytest.raises(AuthError):
        demoted.authenticate(token)
