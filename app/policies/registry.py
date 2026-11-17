"""Which version of each company's policy is live.

The registry is the single source of truth for "what answers right now".
Chunks for a new version are written to the vector store first, while they
are invisible, and only become live when `activate` flips the pointer in one
SQLite transaction. A crash before that point leaves the old version live; a
crash after it leaves the new one live. There is no state in between.

A partial unique index enforces "at most one active version per company" in
the database itself, so even a bug in this class can't make two live.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.errors import VersionConflict
from app.policies.models import PolicyDocument

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_versions (
    company_id   TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    company_name TEXT    NOT NULL,
    doc_id       TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    checksum     TEXT    NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('staging', 'active', 'retired', 'failed')),
    detail       TEXT,
    created_at   TEXT    NOT NULL,
    activated_at TEXT,
    PRIMARY KEY (company_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_version_per_company
    ON policy_versions (company_id) WHERE status = 'active';
"""


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    company_id: str
    company_name: str
    version: int
    doc_id: str
    title: str
    checksum: str
    status: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PolicyRegistry:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        # A short-lived connection per call keeps this safe to use from
        # FastAPI's worker threads without sharing a connection object.
        conn = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # -- reads ------------------------------------------------------------

    def active(self, company_id: str) -> PolicyVersion | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()
        return self._to_version(row) if row else None

    def get(self, company_id: str, version: int) -> PolicyVersion | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE company_id = ? AND version = ?",
                (company_id, version),
            ).fetchone()
        return self._to_version(row) if row else None

    def active_companies(self) -> dict[str, str]:
        """Map of company_id to company name for every company with a live policy."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT company_id, company_name FROM policy_versions WHERE status = 'active'"
            ).fetchall()
        return {r["company_id"]: r["company_name"] for r in rows}

    def staging(self) -> list[PolicyVersion]:
        """Versions that started ingesting but never finished."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM policy_versions WHERE status = 'staging'").fetchall()
        return [self._to_version(r) for r in rows]

    # -- writes -----------------------------------------------------------

    def begin(self, doc: PolicyDocument) -> None:
        """Record that `doc` is being ingested. It is not live yet.

        Versions only move forward: a version at or below the live one is
        refused, so an old document can never be re-activated by accident.
        A version that previously failed or was interrupted may be retried.
        """
        with self._transaction() as conn:
            live = conn.execute(
                "SELECT MAX(version) AS v FROM policy_versions "
                "WHERE company_id = ? AND status IN ('active', 'retired')",
                (doc.company_id,),
            ).fetchone()["v"]
            if live is not None and doc.version <= live:
                raise VersionConflict(f"version {doc.version} is not newer than version {live} for {doc.company_id}")
            conn.execute(
                "DELETE FROM policy_versions WHERE company_id = ? AND version = ? AND status IN ('staging', 'failed')",
                (doc.company_id, doc.version),
            )
            conn.execute(
                "INSERT INTO policy_versions "
                "(company_id, version, company_name, doc_id, title, checksum, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'staging', ?)",
                (doc.company_id, doc.version, doc.company_name, doc.doc_id, doc.title, doc.checksum, _now()),
            )

    def activate(self, company_id: str, version: int) -> int | None:
        """Make `version` the live one and retire the previous. Returns the previous version."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT status FROM policy_versions WHERE company_id = ? AND version = ?",
                (company_id, version),
            ).fetchone()
            if row is None or row["status"] != "staging":
                raise VersionConflict(f"version {version} of {company_id} is not staged")
            previous = conn.execute(
                "SELECT version FROM policy_versions WHERE company_id = ? AND status = 'active'",
                (company_id,),
            ).fetchone()
            conn.execute(
                "UPDATE policy_versions SET status = 'retired' WHERE company_id = ? AND status = 'active'",
                (company_id,),
            )
            conn.execute(
                "UPDATE policy_versions SET status = 'active', activated_at = ? WHERE company_id = ? AND version = ?",
                (_now(), company_id, version),
            )
        return previous["version"] if previous else None

    def mark_failed(self, company_id: str, version: int, detail: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE policy_versions SET status = 'failed', detail = ? "
                "WHERE company_id = ? AND version = ? AND status = 'staging'",
                (detail[:500], company_id, version),
            )

    @staticmethod
    def _to_version(row: sqlite3.Row) -> PolicyVersion:
        return PolicyVersion(
            company_id=row["company_id"],
            company_name=row["company_name"],
            version=row["version"],
            doc_id=row["doc_id"],
            title=row["title"],
            checksum=row["checksum"],
            status=row["status"],
        )
