import pytest

from app.auth import rate_limit
from app.auth.rate_limit import LoginRateLimiter
from app.errors import TooManyAttempts
from tests.helpers import login


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def limiter(clock):
    return LoginRateLimiter(max_failures=3, window_s=60, clock=clock)


def _fail(limiter, client, times):
    for _ in range(times):
        limiter.check(client)
        limiter.record_failure(client)


def test_blocks_after_the_allowed_failures(limiter):
    _fail(limiter, "1.2.3.4", 3)
    with pytest.raises(TooManyAttempts) as blocked:
        limiter.check("1.2.3.4")
    assert blocked.value.retry_after_s == 60


def test_other_clients_are_not_affected(limiter):
    _fail(limiter, "1.2.3.4", 3)
    limiter.check("5.6.7.8")


def test_window_slides(limiter, clock):
    _fail(limiter, "c", 1)
    clock.now += 30
    _fail(limiter, "c", 2)
    with pytest.raises(TooManyAttempts) as blocked:
        limiter.check("c")
    assert blocked.value.retry_after_s == 30
    clock.now += 30
    limiter.check("c")  # the first failure has aged out


def test_idle_clients_are_forgotten_when_the_table_is_full(limiter, clock, monkeypatch):
    monkeypatch.setattr(rate_limit, "_MAX_TRACKED_CLIENTS", 2)
    limiter.record_failure("a")
    limiter.record_failure("b")
    clock.now += 61
    limiter.record_failure("c")
    assert set(limiter._failures) == {"c"}


def test_full_table_of_active_clients_drops_the_oldest(limiter, clock, monkeypatch):
    monkeypatch.setattr(rate_limit, "_MAX_TRACKED_CLIENTS", 2)
    limiter.record_failure("a")
    clock.now += 1
    limiter.record_failure("b")
    limiter.record_failure("c")
    assert set(limiter._failures) == {"b", "c"}


def test_api_returns_429_after_ten_failed_logins(client):
    for _ in range(10):
        assert login(client, "yamuna", "YAM-9999").status_code == 401
    blocked = login(client, "yamuna", "YAM-9999")
    assert blocked.status_code == 429
    assert blocked.json()["status"] == "too_many_attempts"
    assert "5 minutes" in blocked.json()["message"]
    assert blocked.headers["Retry-After"] == "300"
    # A correct ID from the same client is blocked too, until the window passes.
    assert login(client, "yamuna", "YAM-1001").status_code == 429


def test_successful_logins_do_not_count(client):
    for _ in range(15):
        assert login(client, "yamuna", "YAM-1001").status_code == 200
