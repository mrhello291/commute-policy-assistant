"""Telling a mention of another client company from a place with the same name.

Clients are often named after rivers, cities and people, and employees use
those same words in addresses: a Medhavi employee who lives in Gomti Nagar,
or a Gomti employee whose route takes the Yamuna Expressway. A plain word
match refused both questions as being "about another company".

A company counts as named when its full name appears ("Gomti Logistics"), or
its short name, the company id, appears ("Gomti's penalty", "a Yamuna cab")
without an address word straight after it ("Gomti Nagar", "Yamuna
Expressway"). The list of address words is short and covers how Indian
addresses are written; a full name always counts, whatever follows it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

ADDRESS_WORDS = frozenset(
    {
        "bagh", "bank", "barrage", "bazaar", "block", "bridge", "chowk", "circle", "city", "colony",
        "enclave", "expressway", "extension", "ghat", "highway", "hospital", "lane", "layout", "mall",
        "marg", "market", "metro", "nagar", "park", "path", "phase", "riverfront", "river", "road",
        "sector", "station", "street", "tower", "towers", "township", "vihar",
    }
)  # fmt: skip

_NEXT_WORD = re.compile(r"\s+([a-z]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CompanyName:
    company_id: str
    name: str

    def named_in(self, text: str) -> bool:
        if re.search(rf"\b{re.escape(self.name)}\b", text, re.IGNORECASE):
            return True
        for match in re.finditer(rf"\b{re.escape(self.company_id)}\b", text, re.IGNORECASE):
            following = _NEXT_WORD.match(text, match.end())
            if not (following and following.group(1).lower() in ADDRESS_WORDS):
                return True
        return False


def names_any(text: str, companies: Iterable[CompanyName]) -> bool:
    return any(company.named_in(text) for company in companies)
