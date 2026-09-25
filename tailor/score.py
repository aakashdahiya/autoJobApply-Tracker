"""Decide whether a posting is worth applying to, before spending anything on it.

The gate is the cost control and the quality control at once: with the location
filter off, the queue fills faster than anyone can apply, and tailoring for a
40% match wastes both tokens and the reader's goodwill.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from resume.schema import Profile, Shape
from tailor import vocab
from tailor.jd import JobDescription, analyse

REQUIRED_WEIGHT = 0.75
PREFERRED_WEIGHT = 0.25
DEFAULT_THRESHOLD = 50.0

# Asking for a lot more experience than you have is the most common real
# mismatch, and the one a keyword score would otherwise miss entirely.
YEARS_PENALTY_PER_YEAR = 6.0
YEARS_PENALTY_CAP = 30.0


@dataclass
class Score:
    total: float
    shape: Shape
    required_matched: list[str] = field(default_factory=list)
    required_missing: list[str] = field(default_factory=list)
    preferred_matched: list[str] = field(default_factory=list)
    preferred_missing: list[str] = field(default_factory=list)
    years_required: int | None = None
    years_have: float = 0.0
    years_penalty: float = 0.0
    passes: bool = True
    reason: str = ""

    @property
    def gaps(self) -> list[str]:
        """What the posting asks for that nothing in the fact bank supports.

        This is a skip signal and a learning list. It is never an instruction
        to claim the missing thing.
        """
        return self.required_missing


def years_of_experience(profile: Profile, today: dt.date | None = None) -> float:
    """Total professional months in `experience`, in years."""
    today = today or dt.date.today()
    months = 0
    for role in profile.experience:
        start = dt.datetime.strptime(role.start, "%Y-%m").date()
        end = dt.datetime.strptime(role.end, "%Y-%m").date() if role.end else today
        months += max(0, (end.year - start.year) * 12 + (end.month - start.month))
    return round(months / 12, 2)


def capabilities(profile: Profile) -> set[str]:
    """Everything the fact bank can actually back up, in vocabulary terms."""
    terms: set[str] = set()
    for fact in profile.facts:
        terms |= {vocab.canonicalise(skill) for skill in fact.skills}
        terms |= vocab.terms_in(fact.plain)
    for items in profile.skills.values():
        for skill in items:
            terms |= vocab.terms_in(skill)
            terms.add(vocab.canonicalise(skill))
    for role in profile.experience:
        terms |= vocab.terms_in(" ".join(role.tech))
    for project in profile.projects:
        terms |= vocab.terms_in(" ".join(project.tech))
    return terms


def score(
    profile: Profile,
    description: str | JobDescription,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    today: dt.date | None = None,
) -> Score:
    jd = description if isinstance(description, JobDescription) else analyse(description)
    have = capabilities(profile)

    required_matched = sorted(jd.required & have)
    required_missing = sorted(jd.required - have)
    preferred_matched = sorted(jd.preferred & have)
    preferred_missing = sorted(jd.preferred - have)

    # An empty requirement list means the posting named no technology we track.
    # That is a vague posting, not a perfect match, so it scores neutral.
    required_cover = len(required_matched) / len(jd.required) if jd.required else 0.5
    preferred_cover = (
        len(preferred_matched) / len(jd.preferred) if jd.preferred else required_cover
    )

    raw = 100 * (REQUIRED_WEIGHT * required_cover + PREFERRED_WEIGHT * preferred_cover)

    have_years = years_of_experience(profile, today)
    penalty = 0.0
    if jd.years_required and jd.years_required > have_years:
        shortfall = jd.years_required - have_years
        penalty = min(shortfall * YEARS_PENALTY_PER_YEAR, YEARS_PENALTY_CAP)

    total = round(max(0.0, raw - penalty), 1)
    passes = total >= threshold

    if passes:
        reason = f"{len(required_matched)}/{len(jd.required) or '?'} required terms matched"
    elif penalty >= YEARS_PENALTY_CAP / 2 and required_cover >= 0.6:
        reason = (
            f"asks for {jd.years_required} years; the profile shows {have_years:.1f}"
        )
    else:
        missing = ", ".join(required_missing[:4]) or "little overlap"
        reason = f"below threshold — missing {missing}"

    return Score(
        total=total,
        shape=jd.shape,
        required_matched=required_matched,
        required_missing=required_missing,
        preferred_matched=preferred_matched,
        preferred_missing=preferred_missing,
        years_required=jd.years_required,
        years_have=have_years,
        years_penalty=round(penalty, 1),
        passes=passes,
        reason=reason,
    )
