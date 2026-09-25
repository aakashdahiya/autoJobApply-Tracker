"""What kind of reply is this, and is anything in it time-critical?

Rule-based and deterministic. Recruiting email is unusually formulaic, so a
curated phrase list gets most of the way, costs nothing, and can be read and
corrected when it is wrong — none of which is true of a model call on every
message that lands in your inbox.

Ordering matters more than it looks. A rejection almost always opens with
"thank you for applying", so acknowledgement phrases must never outrank
rejection phrases; an offer outranks everything.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

# kind -> (weight, phrases). Weights are deliberately coarse.
SIGNALS: dict[str, list[tuple[float, tuple[str, ...]]]] = {
    "offer": [
        (6.0, (
            "pleased to offer", "offer of employment", "we would like to offer",
            "your offer letter", "formal offer", "extend an offer",
        )),
    ],
    "assessment": [
        (5.0, (
            "online assessment", "coding challenge", "take-home", "take home assignment",
            "technical assessment", "coding assessment", "complete the assessment",
            "hackerrank", "codility", "codesignal", "karat", "woven",
        )),
        (2.0, ("assessment link", "complete this within", "expires in", "you have 72 hours")),
    ],
    "interview_invite": [
        (5.0, (
            "invite you to interview", "schedule an interview", "set up an interview",
            "book a time", "schedule a call", "phone screen", "technical interview",
            "would like to speak with you", "availability for a call",
            "move forward to the interview", "next round",
        )),
        (2.0, ("calendly", "pick a slot", "your availability", "let us know some times")),
    ],
    "rejection": [
        (6.0, (
            "we have decided to move forward with other", "not moving forward",
            "will not be moving forward", "decided not to move forward",
            "no longer under consideration", "unable to offer you",
            "not be proceeding", "pursue other candidates", "other candidates whose",
            "we have filled", "position has been filled", "decided to proceed with other",
            "will not be progressing", "unsuccessful on this occasion",
        )),
        (2.0, ("wish you the best in your", "wish you every success", "keep your resume on file")),
    ],
    "recruiter_outreach": [
        (4.0, (
            "came across your profile", "found your profile", "reaching out about",
            "opportunity that might", "i am a recruiter", "are you open to",
            "would you be interested in a role",
        )),
    ],
    "acknowledgement": [
        (3.0, (
            "we have received your application", "thank you for applying",
            "thanks for applying", "application has been received",
            "your application was submitted", "received your submission",
        )),
    ],
}

# Ties break toward whatever needs a response soonest.
PRIORITY = [
    "offer", "assessment", "interview_invite", "rejection",
    "recruiter_outreach", "acknowledgement", "other",
]

URGENT = {"offer", "assessment", "interview_invite"}

RELATIVE_DEADLINE = re.compile(
    r"\bwithin\s+(\d{1,3})\s*(hour|hours|day|days|business day|business days)\b"
    r"|\b(\d{1,3})\s*(hour|hours|day|days)\s+to\s+complete\b"
    r"|\bexpires?\s+in\s+(\d{1,3})\s*(hour|hours|day|days)\b"
    r"|\byou\s+have\s+(\d{1,3})\s*(hour|hours|day|days)\b",
    re.I,
)

# Where a reply stops being new text and starts being the thread's history.
QUOTED = re.compile(
    r"^\s*>"
    r"|^\s*On\s.{0,120}\swrote:\s*$"
    r"|^\s*-{2,}\s*(original message|forwarded message)"
    r"|^\s*From:\s.+$"
    r"|^\s*_{5,}\s*$",
    re.I | re.M,
)
ABSOLUTE_DEADLINE = re.compile(
    r"\b(?:by|before|due(?:\s+on)?|no later than|deadline[:\s]+)\s+"
    r"((?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"|\d{4}-\d{2}-\d{2})",
    re.I,
)
SCHEDULING_LINK = re.compile(
    r"https?://(?:[\w.-]*\.)?(calendly\.com|savvycal\.com|cal\.com|hubspot\.com/meetings"
    r"|goodtime\.io|greenhouse\.io/scheduling)[^\s>\"')]*",
    re.I,
)
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


@dataclass
class Triage:
    kind: str = "other"
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    deadline_at: dt.datetime | None = None
    deadline_text: str = ""
    scheduling_links: list[str] = field(default_factory=list)
    urgent: bool = False


def strip_quoted(body: str) -> str:
    """Drop the quoted history below a reply.

    Truncating alone is not enough: a two-line "we would like to speak with
    you" followed by a quoted rejection puts both verdicts inside the first
    2000 characters, and the older, longer one wins. The new text is the only
    text that says anything about now.
    """
    match = QUOTED.search(body or "")
    return (body or "")[: match.start()] if match else (body or "")


def _blob(subject: str, body: str, *, body_chars: int = 2000) -> str:
    """Subject plus the opening of the new text in the body."""
    fresh = strip_quoted(body)[:body_chars]
    return re.sub(r"\s+", " ", f"{subject or ''}\n{fresh}").casefold()


def find_deadline(text: str, *, received_at: dt.datetime | None = None) -> tuple[dt.datetime | None, str]:
    """The thing most expensive to miss: an assessment window."""
    received = received_at or dt.datetime.now(dt.timezone.utc)
    if received.tzinfo is None:
        received = received.replace(tzinfo=dt.timezone.utc)

    match = RELATIVE_DEADLINE.search(text)
    if match:
        groups = [g for g in match.groups() if g]
        amount = int(next(g for g in groups if g.isdigit()))
        unit = next(g for g in groups if not g.isdigit()).casefold()
        delta = dt.timedelta(hours=amount) if unit.startswith("hour") else dt.timedelta(days=amount)
        return received + delta, match.group(0).strip()

    match = ABSOLUTE_DEADLINE.search(text)
    if match:
        raw = match.group(1).strip()
        parsed = _parse_absolute(raw, received)
        return parsed, raw
    return None, ""


def _parse_absolute(raw: str, received: dt.datetime) -> dt.datetime | None:
    folded = raw.casefold()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", folded):
        try:
            return dt.datetime.strptime(folded, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return None

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if folded in weekdays:
        ahead = (weekdays.index(folded) - received.weekday()) % 7 or 7
        return received + dt.timedelta(days=ahead)

    day_month = re.fullmatch(r"(\d{1,2})\s+([a-z]+)", folded)
    month_day = re.fullmatch(r"([a-z]+)\s+(\d{1,2})", folded)
    pair = None
    if day_month:
        pair = (int(day_month.group(1)), day_month.group(2)[:3])
    elif month_day:
        pair = (int(month_day.group(2)), month_day.group(1)[:3])
    if pair and pair[1] in MONTHS:
        day, month = pair[0], MONTHS.index(pair[1]) + 1
        for year in (received.year, received.year + 1):
            try:
                candidate = dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
            except ValueError:
                return None
            if candidate >= received - dt.timedelta(days=1):
                return candidate
    return None


def triage(subject: str, body: str, *, received_at: dt.datetime | None = None) -> Triage:
    text = _blob(subject, body)
    scores = {kind: 0.0 for kind in SIGNALS}
    for kind, groups in SIGNALS.items():
        for weight, phrases in groups:
            for phrase in phrases:
                if phrase in text:
                    scores[kind] += weight
                    break

    best = max(scores, key=lambda k: (scores[k], -PRIORITY.index(k)))
    total = sum(scores.values())
    if scores[best] == 0:
        return Triage(kind="other", scores=scores)

    deadline_at, deadline_text = find_deadline(text, received_at=received_at)
    links = SCHEDULING_LINK.findall(f"{subject or ''} {body or ''}")
    return Triage(
        kind=best,
        confidence=round(scores[best] / total, 3) if total else 0.0,
        scores={k: round(v, 1) for k, v in scores.items() if v},
        deadline_at=deadline_at,
        deadline_text=deadline_text,
        scheduling_links=[m if isinstance(m, str) else m[0] for m in links][:3],
        urgent=best in URGENT,
    )
