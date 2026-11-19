"""Loads the bundled sample policies when the service starts.

Each company folder holds `v1.md`, `v2.md`, ... and they are ingested in
version order, so the newest one ends up live. Running it again is harmless:
versions that are already live or retired are skipped. Folders for companies
that aren't clients (not in the employee directory) are skipped too.
"""

import logging
import re
from pathlib import Path

from app.errors import AppError, VersionConflict
from app.policies.ingestion import IngestionReport, IngestionService

log = logging.getLogger(__name__)

_VERSION_FILE = re.compile(r"^v(\d+)\.md$")


def policy_files(seed_dir: Path) -> list[Path]:
    """Every `<company>/v<N>.md` under `seed_dir`, ordered by company then version."""
    found = []
    for company_dir in sorted(p for p in seed_dir.iterdir() if p.is_dir()):
        for path in company_dir.iterdir():
            match = _VERSION_FILE.match(path.name)
            if match:
                found.append((company_dir.name, int(match.group(1)), path))
    return [path for _, _, path in sorted(found)]


class PolicySeeder:
    def __init__(self, ingestion: IngestionService):
        self._ingestion = ingestion

    def seed(self, seed_dir: Path, companies: set[str] | None = None) -> list[IngestionReport]:
        """Load every policy file, or only those of `companies` when given."""
        if not seed_dir.is_dir():
            log.warning("policy seed directory %s not found, nothing loaded", seed_dir)
            return []

        reports = []
        skipped = set()
        for path in policy_files(seed_dir):
            if companies is not None and path.parent.name not in companies:
                skipped.add(path.parent.name)
                continue
            try:
                reports.append(self._ingestion.ingest(path.read_text(encoding="utf-8")))
            except VersionConflict:
                log.info("skipping %s, a newer version is already live", path)
            except AppError:
                # One broken file shouldn't stop the other companies loading.
                log.exception("could not load %s", path)
        if skipped:
            log.warning("not loading policies for %s: not in the employee directory", ", ".join(sorted(skipped)))
        return reports
