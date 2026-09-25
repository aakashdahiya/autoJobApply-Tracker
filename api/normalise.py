"""Turning messy posting text into keys that match across sources.

The same job appears on LinkedIn, the company site and a job board with three
different spellings. These functions decide when two of them are the same job.
"""

from __future__ import annotations

import hashlib
import re

# Dropped from a company name before comparing. "Shopify" and "Shopify Inc."
# are one employer.
COMPANY_SUFFIXES = {
    "inc", "inc.", "llc", "ltd", "ltd.", "limited", "corp", "corp.", "corporation",
    "co", "co.", "company", "technologies", "technology", "labs", "lab", "software",
    "solutions", "systems", "group", "holdings", "canada", "ca", "plc", "gmbh", "sa",
}

# Seniority spellings that mean the same thing.
TITLE_SYNONYMS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "jnr": "junior",
    "eng": "engineer", "engineering": "engineer", "dev": "developer",
    "devops": "devops", "sw": "software", "swe": "software engineer",
    "mle": "machine learning engineer", "ml": "machine learning",
    "ai": "artificial intelligence", "sde": "software engineer",
}

# Noise that says nothing about which job this is.
TITLE_NOISE = {
    "remote", "hybrid", "onsite", "contract", "permanent", "intern", "coop",
    "new", "urgent", "hiring", "w", "m", "f", "d", "x",
}

# Employment-type phrases, stripped before splitting into words. Doing this at
# phrase level matters: "Full Time" is two tokens, and dropping the bare word
# "time" would also wreck "Real Time Systems Engineer".
EMPLOYMENT_PHRASES = re.compile(
    r"\b(?:full|part)[\s\-]?time\b|\bco[\s\-]?op\b|\bon[\s\-]?site\b"
    r"|\binternship\b|\bw/?\s?m/?\s?[dfx]\b",
    re.I,
)

CANADIAN_LOCATIONS = {
    "toronto": "toronto, on", "gta": "toronto, on", "greater toronto area": "toronto, on",
    "north york": "toronto, on", "scarborough": "toronto, on", "etobicoke": "toronto, on",
    "mississauga": "mississauga, on", "brampton": "brampton, on", "markham": "markham, on",
    "ottawa": "ottawa, on", "kitchener": "kitchener, on", "waterloo": "waterloo, on",
    "hamilton": "hamilton, on", "london": "london, on", "ajax": "ajax, on",
    "montreal": "montreal, qc", "montréal": "montreal, qc", "mtl": "montreal, qc",
    "quebec city": "quebec city, qc", "québec": "quebec city, qc",
    "vancouver": "vancouver, bc", "burnaby": "burnaby, bc", "victoria": "victoria, bc",
    "calgary": "calgary, ab", "edmonton": "edmonton, ab",
    "winnipeg": "winnipeg, mb", "halifax": "halifax, ns", "saskatoon": "saskatoon, sk",
    "thunder bay": "thunder bay, on",
}

PROVINCE_CODES = {
    "ontario": "on", "quebec": "qc", "québec": "qc", "british columbia": "bc",
    "alberta": "ab", "manitoba": "mb", "saskatchewan": "sk", "nova scotia": "ns",
    "new brunswick": "nb", "newfoundland and labrador": "nl",
    "prince edward island": "pe", "yukon": "yt", "northwest territories": "nt",
    "nunavut": "nu",
}

REMOTE_HINTS = ("remote", "work from home", "wfh", "anywhere", "distributed")
HYBRID_HINTS = ("hybrid", "flexible")

_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")
_PARENS = re.compile(r"\([^)]*\)")


def _words(text: str, *, strip_phrases: bool = False) -> list[str]:
    text = _PARENS.sub(" ", text.casefold())
    if strip_phrases:
        text = EMPLOYMENT_PHRASES.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip().split()


def company_norm(name: str) -> str:
    """`Shopify Inc.` and `shopify` collapse to one key."""
    words = [w for w in _words(name) if w not in COMPANY_SUFFIXES]
    return " ".join(words) or name.casefold().strip()


def title_norm(title: str) -> str:
    """`Sr. ML Eng (Remote)` and `Senior Machine Learning Engineer` collapse."""
    out: list[str] = []
    for word in _words(title, strip_phrases=True):
        if word in TITLE_NOISE:
            continue
        expanded = TITLE_SYNONYMS.get(word, word)
        out.extend(expanded.split())
    # Drop trailing level markers: "engineer ii", "engineer 3".
    while out and (out[-1] in {"i", "ii", "iii", "iv", "v"} or out[-1].isdigit()):
        out.pop()
    deduped: list[str] = []
    for word in out:  # "machine learning engineer engineer" -> one engineer
        if not deduped or deduped[-1] != word:
            deduped.append(word)
    return " ".join(deduped) or title.casefold().strip()


def location_norm(location: str) -> str:
    """Canonical `city, prov`, or `remote - canada`."""
    raw = location.casefold().strip()
    if not raw:
        return ""
    if any(h in raw for h in REMOTE_HINTS):
        return "remote - canada" if "canada" in raw or "," not in raw else raw
    cleaned = _WS.sub(" ", _PUNCT.sub(" ", raw)).strip()
    if cleaned in CANADIAN_LOCATIONS:
        return CANADIAN_LOCATIONS[cleaned]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if parts:
        city = _WS.sub(" ", _PUNCT.sub(" ", parts[0])).strip()
        if city in CANADIAN_LOCATIONS:
            return CANADIAN_LOCATIONS[city]
        if len(parts) > 1:
            region = parts[1].strip()
            code = PROVINCE_CODES.get(region, region)
            return f"{city}, {code}"
        return city
    return cleaned


def remote_type(location: str, description: str = "") -> str:
    blob = f"{location} {description}".casefold()
    if any(h in blob for h in REMOTE_HINTS):
        return "remote"
    if any(h in blob for h in HYBRID_HINTS):
        return "hybrid"
    return "onsite"


def dedupe_key(company: str, title: str) -> str:
    """Location is deliberately absent.

    Enterprises publish one requisition per city. Keying on location turns one
    job into five rows, five tailored resumes, and five chances to apply twice.
    """
    return hashlib.sha1(
        f"{company_norm(company)}|{title_norm(title)}".encode()
    ).hexdigest()


def description_hash(description: str) -> str:
    collapsed = _WS.sub(" ", (description or "").casefold()).strip()
    return hashlib.sha1(collapsed.encode()).hexdigest()
