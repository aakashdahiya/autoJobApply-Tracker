"""Read the open roles from a company's public job board.

Each ATS returns a different JSON shape, and those shapes drift. Rather than
hard-coding one field name per value, every field lists the keys it might
appear under and the first one present wins. A vendor renaming `absolute_url`
to `url` then costs one entry in a list instead of a crash at 2am, and a field
that disappears degrades to empty rather than taking the crawl down.

The shapes below are written from the vendors' documented board APIs; the
first real run is the moment to confirm them, and `--probe` prints what came
back so that takes a minute rather than an afternoon.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Callable

TAGS = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    """Board descriptions arrive as HTML, sometimes double-escaped."""
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", text, flags=re.I)
    text = TAGS.sub(" ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def dig(payload: Any, path: str) -> Any:
    """Follow a dotted path, tolerating missing links and lists."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and current:
            current = current[0]
            if isinstance(current, dict):
                current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def first(payload: dict, paths: tuple[str, ...], transform: Callable | None = None) -> str:
    for path in paths:
        value = dig(payload, path)
        if isinstance(value, (str, int, float)) and str(value).strip():
            text = str(value).strip()
            return transform(text) if transform else text
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return ", ".join(value)
    return ""


@dataclass(frozen=True)
class BoardSpec:
    ats: str
    url: str                       # .format(slug=...)
    items: tuple[str, ...]         # where the list of jobs lives ("" = top level)
    title: tuple[str, ...]
    location: tuple[str, ...]
    apply_url: tuple[str, ...]
    description: tuple[str, ...] = ()
    posted_at: tuple[str, ...] = ()
    detail_url: str = ""           # per-job fetch when the list omits the body


BOARDS: dict[str, BoardSpec] = {
    "greenhouse": BoardSpec(
        ats="greenhouse",
        url="https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        items=("jobs",),
        title=("title", "name"),
        location=("location.name", "location", "offices.name"),
        apply_url=("absolute_url", "url"),
        description=("content", "description"),
        posted_at=("updated_at", "first_published", "created_at"),
    ),
    "lever": BoardSpec(
        ats="lever",
        url="https://api.lever.co/v0/postings/{slug}?mode=json",
        items=("",),
        title=("text", "title"),
        location=("categories.location", "workplaceType", "country"),
        apply_url=("hostedUrl", "applyUrl", "url"),
        description=("descriptionPlain", "description", "plainText"),
        posted_at=("createdAt", "postedAt"),
    ),
    "ashby": BoardSpec(
        ats="ashby",
        url="https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
        items=("jobs",),
        title=("title", "jobTitle"),
        location=("location", "locationName", "address.postalAddress.addressLocality"),
        apply_url=("jobUrl", "applyUrl", "url"),
        description=("descriptionPlain", "descriptionHtml", "description"),
        posted_at=("publishedAt", "updatedAt"),
    ),
    "workable": BoardSpec(
        ats="workable",
        url="https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        items=("jobs",),
        title=("title", "name"),
        location=("city", "location.city", "country"),
        apply_url=("url", "shortlink", "application_url"),
        description=("description", "full_description", "requirements"),
        posted_at=("published_on", "created_at"),
    ),
    "smartrecruiters": BoardSpec(
        ats="smartrecruiters",
        url="https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        items=("content",),
        title=("name", "title"),
        location=("location.city", "location.region", "location.country"),
        apply_url=("ref", "applyUrl", "postingUrl"),
        description=("jobAd.sections.jobDescription.text", "releasedDate"),
        posted_at=("releasedDate", "createdOn"),
        detail_url="https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}",
    ),
    "recruitee": BoardSpec(
        ats="recruitee",
        url="https://{slug}.recruitee.com/api/offers/",
        items=("offers",),
        title=("title", "position"),
        location=("location", "city", "country"),
        apply_url=("careers_url", "careers_apply_url", "url"),
        description=("description", "requirements"),
        posted_at=("published_at", "created_at"),
    ),
}


@dataclass
class Posting:
    title: str
    location: str = ""
    apply_url: str = ""
    description: str = ""
    posted_at: str = ""
    ats: str = ""
    company: str = ""
    raw_id: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.title and self.apply_url)


@dataclass
class BoardResult:
    company: str
    ats: str
    postings: list[Posting] = field(default_factory=list)
    error: str = ""
    dropped: int = 0


def parse(spec: BoardSpec, payload: Any, company: str) -> list[Posting]:
    """Turn one board response into postings, skipping anything unusable."""
    items: Any = payload
    for key in spec.items:
        if key:
            items = dig(items, key)
    if items is None:
        return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []

    postings = []
    for item in items:
        if not isinstance(item, dict):
            continue
        postings.append(
            Posting(
                title=first(item, spec.title),
                location=first(item, spec.location),
                apply_url=first(item, spec.apply_url),
                description=strip_html(first(item, spec.description)),
                posted_at=first(item, spec.posted_at),
                ats=spec.ats,
                company=company,
                raw_id=str(item.get("id") or item.get("uuid") or ""),
            )
        )
    return postings


def fetch_board(company: str, ats: str, slug: str, *, fetch) -> BoardResult:
    spec = BOARDS.get(ats)
    if spec is None:
        return BoardResult(company, ats, error=f"no reader for {ats!r}")

    payload = fetch(spec.url.format(slug=slug))
    if payload is None:
        return BoardResult(company, ats, error="board did not respond with JSON")

    postings = parse(spec, payload, company)
    usable = [p for p in postings if p.usable]
    return BoardResult(company, ats, usable, dropped=len(postings) - len(usable))
