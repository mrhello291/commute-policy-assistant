"""The sample policies do what the brief asks of them.

Three companies with overlapping terms and deliberately contradictory rules,
"so any cross-company leak shows". If two companies agreed on a rule, an
answer leaked from the wrong one would look correct and the isolation tests
would prove nothing about it.
"""

import re

from app.policies.pdf_parser import PdfPolicyParser
from tests.conftest import POLICY_DIR

COMPANIES = ["yamuna", "gomti", "medhavi"]
TIME = re.compile(r"after (\d{1,2}:\d{2} [AP]M)")


def section(company: str, title: str, version: int | None = None):
    files = sorted((POLICY_DIR / company).glob("v*.pdf"), key=lambda p: int(p.stem[1:]))
    path = POLICY_DIR / company / f"v{version}.pdf" if version else files[-1]
    doc = PdfPolicyParser().parse(path.read_bytes())
    [match] = [s for s in doc.sections if s.title == title]
    return match.body


def escort_time(company: str, version: int | None = None) -> str:
    return TIME.search(section(company, "Security escort", version)).group(1)


def test_every_company_covers_the_same_topic_with_a_different_rule():
    times = {company: escort_time(company) for company in COMPANIES}
    assert len(set(times.values())) == len(COMPANIES), times


def test_the_new_version_changes_a_rule_the_old_one_had():
    """Yamuna v2 must differ from v1, or "only the new version answers" couldn't be observed."""
    assert escort_time("yamuna", 1) != escort_time("yamuna", 2)
