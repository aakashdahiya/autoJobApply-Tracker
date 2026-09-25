"""What to keep from a company's whole board.

Cohere's board lists roles in San Francisco and London; the watchlist is
Canadian and the search is Canada-wide. Without these two filters the nightly
run buys a bigger queue rather than a better one, and the score gate ends up
doing work that a string comparison should have done for free.
"""

from __future__ import annotations

import re

from api.normalise import CANADIAN_LOCATIONS

CANADA_WORDS = {"canada", "canadian", "ontario", "quebec", "québec", "british columbia",
                "alberta", "manitoba", "saskatchewan", "nova scotia", "new brunswick",
                "newfoundland", "yukon", "nunavut"}
PROVINCE_CODES = {"on", "qc", "bc", "ab", "mb", "sk", "ns", "nb", "nl", "pe", "yt", "nt", "nu"}

# Places that make a "remote" posting not a Canadian one.
ELSEWHERE = {
    "united states", "usa", "u.s.", "us-based", "new york", "san francisco", "seattle",
    "austin", "boston", "chicago", "denver", "atlanta", "los angeles", "california",
    "texas", "washington", "united kingdom", "england", "manchester", "ireland",
    "dublin", "germany", "berlin", "munich", "france", "paris", "netherlands",
    "amsterdam", "spain", "portugal", "poland", "india", "bangalore", "bengaluru",
    "hyderabad", "pune", "singapore", "australia", "japan", "tokyo",
    "brazil", "mexico", "emea", "apac", "latam", "united arab emirates",
}
# "london" and "sydney" are deliberately absent: they are also Canadian and
# Nova Scotian cities, so they are resolved by AMBIGUOUS_CITIES below rather
# than being ruled out on sight.
REMOTE_WORDS = {"remote", "anywhere", "distributed", "work from home", "telecommute"}

# Canadian city names that are also somewhere else. "London" is a real Ontario
# city and a much more famous English one, so a bare "London" is not evidence
# of anything — these need a province or a country to count.
AMBIGUOUS_CITIES = {
    "london", "hamilton", "victoria", "windsor", "kingston", "waterloo",
    "cambridge", "richmond", "surrey", "stratford", "perth", "chatham",
    "sydney", "birmingham",
}

# Country codes worth spotting, matched as whole words.
ELSEWHERE_CODES = re.compile(r"(?<![a-z])(uk|u\.k\.|usa|u\.s\.a\.|us|ie|de|fr|in|au|sg)(?![a-z])")

# Titles worth a look, given the three shapes in the fact bank.
ROLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|scientist|architect|sde|swe)\b", re.I
)
# Deliberately wide. The score gate is the real filter, so a title that only
# might be relevant is cheap to let through, while one wrongly excluded here is
# a job never seen at all.
FIELD_WORDS = re.compile(
    r"\b(software|python|backend|back[- ]end|full[- ]?stack|fullstack|web|api|platform"
    r"|ai|a\.i\.|ml|machine learning|deep learning|nlp|llm|genai|generative"
    r"|data|applied scientist|research engineer|infrastructure"
    r"|systems?|distributed|cloud|server|services?|product)\b",
    re.I,
)
# Roles that match the words above but are not this search.
EXCLUDE_TITLE = re.compile(
    r"\b(sales|account executive|recruit|talent acquisition|designer|ux|ui designer"
    r"|marketing|customer success|support engineer|solutions? engineer|sales engineer"
    r"|hardware|mechanical|electrical|civil|chemical|firmware|asic|rf |analog"
    r"|director|vice president|vp,|head of|principal architect|manager,|engineering manager"
    r"|qa |quality assurance|sdet|technician|technologist|nurse|teacher)\b",
    re.I,
)
SENIOR_TOO_FAR = re.compile(r"\b(staff|principal|distinguished|fellow|lead)\b", re.I)


def location_verdict(location: str) -> str:
    """`canada`, `remote` (unqualified) or `elsewhere`."""
    text = re.sub(r"\s+", " ", (location or "").casefold())
    if not text:
        return "remote"  # boards often leave it blank on remote roles

    unambiguous_city = any(
        city in text for city in CANADIAN_LOCATIONS if city not in AMBIGUOUS_CITIES
    )
    definitely_canada = (
        any(word in text for word in CANADA_WORDS)
        or unambiguous_city
        or any(re.search(rf"(?<![a-z]){code}(?![a-z])", text) for code in PROVINCE_CODES)
    )
    if definitely_canada:
        return "canada"

    elsewhere = any(place in text for place in ELSEWHERE) or bool(ELSEWHERE_CODES.search(text))
    if elsewhere:
        return "elsewhere"

    # An ambiguous city with nothing contradicting it: assume the Canadian one,
    # since every company on this watchlist is Canadian.
    if any(city in text for city in AMBIGUOUS_CITIES):
        return "canada"
    return "remote" if any(word in text for word in REMOTE_WORDS) else "elsewhere"


def relevant_title(title: str) -> bool:
    text = title or ""
    if EXCLUDE_TITLE.search(text):
        return False
    return bool(ROLE_WORDS.search(text) and FIELD_WORDS.search(text))


def keep(title: str, location: str) -> tuple[bool, str]:
    """Should this posting enter the queue, and if not, why not?"""
    if not relevant_title(title):
        return False, "title is not one of the three shapes"
    verdict = location_verdict(location)
    if verdict == "elsewhere":
        return False, f"location outside Canada ({location})"
    return True, verdict
