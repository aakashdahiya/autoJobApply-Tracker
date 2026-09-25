"""Public job-board endpoints, and how to read a job count out of each.

Only endpoints the vendors publish for their customers' own career pages are
used here. No scraping, no authentication, no logged-in sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

JsonExtractor = Callable[[object], int | None]


def _count_key(key: str) -> JsonExtractor:
    def extract(payload: object) -> int | None:
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return len(payload[key])
        return None

    return extract


def _count_list(payload: object) -> int | None:
    return len(payload) if isinstance(payload, list) else None


@dataclass(frozen=True)
class Probe:
    ats: str
    url: str  # .format(slug=...)
    extract: JsonExtractor


PROBES: tuple[Probe, ...] = (
    Probe(
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
        _count_key("jobs"),
    ),
    Probe("lever", "https://api.lever.co/v0/postings/{slug}?mode=json", _count_list),
    Probe(
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        _count_key("jobs"),
    ),
    Probe(
        "workable",
        "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        _count_key("jobs"),
    ),
    Probe(
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        _count_key("content"),
    ),
    Probe("recruitee", "https://{slug}.recruitee.com/api/offers/", _count_key("offers")),
)
