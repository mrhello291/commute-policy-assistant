from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth import Principal, Role, TokenIssuer, TokenVerifier
from app.errors import AuthError

SECRET = "test-secret-that-is-at-least-32-bytes-long"
ISSUER = "test-idp"
AUDIENCE = "test-api"


@pytest.fixture
def issuer():
    return TokenIssuer(SECRET, ISSUER, AUDIENCE, ttl=timedelta(minutes=5))


@pytest.fixture
def verifier():
    return TokenVerifier(SECRET, ISSUER, AUDIENCE)


def _claims(**overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": "emp-1",
        "company_id": "arcadia",
        "role": "employee",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def test_round_trip(issuer, verifier):
    token = issuer.issue("emp-1", "arcadia", Role.POLICY_ADMIN)
    assert verifier.verify(token) == Principal("emp-1", "arcadia", Role.POLICY_ADMIN)


def test_expired_token_rejected(verifier):
    token = jwt.encode(_claims(exp=datetime.now(UTC) - timedelta(seconds=1)), SECRET, "HS256")
    with pytest.raises(AuthError, match="expired"):
        verifier.verify(token)


def test_wrong_signature_rejected(verifier):
    token = jwt.encode(_claims(), "some-other-secret-value-also-32-bytes-long", "HS256")
    with pytest.raises(AuthError):
        verifier.verify(token)


def test_unsigned_token_rejected(verifier):
    token = jwt.encode(_claims(), None, algorithm="none")
    with pytest.raises(AuthError):
        verifier.verify(token)


def test_wrong_audience_rejected(verifier):
    token = jwt.encode(_claims(aud="another-service"), SECRET, "HS256")
    with pytest.raises(AuthError):
        verifier.verify(token)


@pytest.mark.parametrize("claim", ["company_id", "sub", "role", "exp"])
def test_missing_claim_rejected(verifier, claim):
    claims = _claims()
    del claims[claim]
    with pytest.raises(AuthError):
        verifier.verify(jwt.encode(claims, SECRET, "HS256"))


@pytest.mark.parametrize("field,value", [("company_id", ""), ("role", "superuser")])
def test_bad_claim_values_rejected(verifier, field, value):
    with pytest.raises(AuthError):
        verifier.verify(jwt.encode(_claims(**{field: value}), SECRET, "HS256"))


def test_garbage_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify("not-a-jwt")
