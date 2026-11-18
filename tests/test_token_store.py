import sqlite3

import pytest

from app.auth.token_store import TokenRecord, TokenStore
from app.errors import AuthUnavailable

NOW = 1_800_000_000


def _record(jti, employee_id="YAM-1001", issued_at=NOW, ttl=3600):
    return TokenRecord(
        jti=jti,
        employee_id=employee_id,
        company_id="yamuna",
        role="employee",
        issued_at=issued_at,
        expires_at=issued_at + ttl,
        issued_by="test-idp",
        client_ip="127.0.0.1",
        user_agent="pytest",
    )


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "auth.db")


def test_record_and_read_back(store):
    store.record(_record("a"), max_live_per_employee=5)
    got = store.get("a")
    assert got == _record("a")
    assert got.is_live(NOW + 1)
    assert not got.is_live(NOW + 3600)


def test_unknown_jti_is_none(store):
    assert store.get("missing") is None


def test_records_survive_a_new_store_instance(tmp_path):
    TokenStore(tmp_path / "auth.db").record(_record("a"), max_live_per_employee=5)
    assert TokenStore(tmp_path / "auth.db").get("a") is not None


def test_revoke_is_one_shot(store):
    store.record(_record("a"), max_live_per_employee=5)
    assert store.revoke("a", "logout", NOW + 5) is True
    assert store.revoke("a", "logout", NOW + 6) is False
    got = store.get("a")
    assert (got.revoked_at, got.revoked_reason) == (NOW + 5, "logout")
    assert not got.is_live(NOW + 10)


def test_oldest_tokens_beyond_the_limit_are_revoked(store):
    for i in range(3):
        assert store.record(_record(f"t{i}", issued_at=NOW + i), max_live_per_employee=2) == ([] if i < 2 else ["t0"])
    assert store.get("t0").revoked_reason == "session_limit"
    assert store.live_count("YAM-1001", NOW + 3) == 2


def test_limit_is_per_employee(store):
    store.record(_record("a", employee_id="YAM-1001"), max_live_per_employee=1)
    assert store.record(_record("b", employee_id="YAM-1002"), max_live_per_employee=1) == []


def test_expired_tokens_do_not_count_towards_the_limit(store):
    store.record(_record("old", ttl=10), max_live_per_employee=1)
    assert store.record(_record("new", issued_at=NOW + 20), max_live_per_employee=1) == []
    assert store.get("old").revoked_at is None


def test_purge_removes_only_long_dead_records(store):
    store.record(_record("expired", ttl=10), max_live_per_employee=5)
    store.record(_record("live"), max_live_per_employee=5)
    assert store.purge(expired_before=NOW + 100) == 1
    assert store.get("expired") is None
    assert store.get("live") is not None


def test_duplicate_jti_is_refused(store):
    store.record(_record("a"), max_live_per_employee=5)
    with pytest.raises(AuthUnavailable):
        store.record(_record("a"), max_live_per_employee=5)


def test_broken_database_fails_closed(tmp_path):
    db = tmp_path / "auth.db"
    store = TokenStore(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE issued_tokens")
    with pytest.raises(AuthUnavailable):
        store.get("a")
