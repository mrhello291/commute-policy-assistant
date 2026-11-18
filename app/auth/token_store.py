"""A durable record of every token the login endpoint has issued.

A JWT on its own can't be taken back before it expires. Recording each one
by its `jti` lets the API refuse a token after logout, after the employee
signs in on too many devices, or after the record is removed. The table is
in SQLite on the data volume, so all of this survives a restart or a crash.

The token string itself is never stored, only its id and claims: a copy of
this database is not a copy of anyone's session.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.errors import AuthUnavailable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issued_tokens (
    jti            TEXT    PRIMARY KEY,
    employee_id    TEXT    NOT NULL,
    company_id     TEXT    NOT NULL,
    role           TEXT    NOT NULL,
    issued_at      INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    issued_by      TEXT    NOT NULL,
    client_ip      TEXT,
    user_agent     TEXT,
    revoked_at     INTEGER,
    revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS issued_tokens_by_employee ON issued_tokens (employee_id, issued_at);
"""


@dataclass(frozen=True, slots=True)
class TokenRecord:
    jti: str
    employee_id: str
    company_id: str
    role: str
    issued_at: int  # unix seconds
    expires_at: int
    issued_by: str
    client_ip: str | None = None
    user_agent: str | None = None
    revoked_at: int | None = None
    revoked_reason: str | None = None

    def is_live(self, now: int) -> bool:
        return self.revoked_at is None and self.expires_at > now


class TokenStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection and transaction per call.

        Any database problem becomes `AuthUnavailable`, so a broken store
        makes requests fail closed with a "try again" instead of letting
        an unchecked token through.
        """
        try:
            conn = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        except sqlite3.Error as exc:
            raise AuthUnavailable("token store unavailable") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            _rollback(conn)
            raise AuthUnavailable("token store unavailable") from exc
        except BaseException:
            _rollback(conn)
            raise
        finally:
            conn.close()

    def record(self, token: TokenRecord, max_live_per_employee: int) -> list[str]:
        """Store a newly issued token and revoke the employee's oldest live
        tokens beyond `max_live_per_employee`. Returns the revoked ids."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO issued_tokens (jti, employee_id, company_id, role, issued_at, expires_at, "
                "issued_by, client_ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    token.jti,
                    token.employee_id,
                    token.company_id,
                    token.role,
                    token.issued_at,
                    token.expires_at,
                    token.issued_by,
                    token.client_ip,
                    token.user_agent,
                ),
            )
            live = conn.execute(
                "SELECT jti FROM issued_tokens WHERE employee_id = ? AND revoked_at IS NULL AND expires_at > ? "
                "ORDER BY issued_at DESC, rowid DESC",
                (token.employee_id, token.issued_at),
            ).fetchall()
            surplus = [row["jti"] for row in live[max_live_per_employee:]]
            conn.executemany(
                "UPDATE issued_tokens SET revoked_at = ?, revoked_reason = 'session_limit' WHERE jti = ?",
                [(token.issued_at, jti) for jti in surplus],
            )
        return surplus

    def get(self, jti: str) -> TokenRecord | None:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM issued_tokens WHERE jti = ?", (jti,)).fetchone()
        return TokenRecord(**dict(row)) if row else None

    def revoke(self, jti: str, reason: str, now: int) -> bool:
        """Revoke one token. Returns False if it was unknown or already revoked."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE issued_tokens SET revoked_at = ?, revoked_reason = ? WHERE jti = ? AND revoked_at IS NULL",
                (now, reason, jti),
            )
        return cursor.rowcount == 1

    def live_count(self, employee_id: str, now: int) -> int:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM issued_tokens "
                "WHERE employee_id = ? AND revoked_at IS NULL AND expires_at > ?",
                (employee_id, now),
            ).fetchone()
        return row["n"]

    def purge(self, expired_before: int) -> int:
        """Delete records that stopped being usable before `expired_before`.

        Run at startup. Recent records are kept for a while as an audit trail.
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM issued_tokens WHERE expires_at < ? OR revoked_at < ?",
                (expired_before, expired_before),
            )
        return cursor.rowcount


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass  # no transaction was open
