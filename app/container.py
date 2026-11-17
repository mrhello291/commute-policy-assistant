"""Builds the application's object graph in one place.

There is no DI framework: a plain class creates each collaborator once and
hands it to whatever needs it. Tests build their own `Container` with fakes.
"""

from datetime import timedelta

from app.auth import TokenIssuer, TokenVerifier
from app.config import Settings


class Container:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_issuer = TokenIssuer(
            secret=settings.jwt_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            ttl=timedelta(minutes=settings.jwt_ttl_minutes),
        )
        self.token_verifier = TokenVerifier(
            secret=settings.jwt_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
