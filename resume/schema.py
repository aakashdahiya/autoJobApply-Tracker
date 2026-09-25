"""Schema for `profile.yaml` — the fact bank every resume is built from.

Nothing may reach a rendered resume that does not trace back to a `Fact` here.
`Profile` enforces the structural half of that (facts reference real roles,
fact skills are declared up front); `verify.py` enforces the rest at render
time.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

YYYY_MM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class Shape(str, Enum):
    """The three role shapes Canadian AI/Python hiring splits into.

    One shape per rendered resume, never three — see docs/ARCHITECTURE.md
    section 9. Breadth lives in the fact bank; focus lives in the render.
    """

    research = "research"
    ml_platform = "ml_platform"
    product_python = "product_python"


class PermitType(str, Enum):
    open = "open"
    employer_specific = "employer_specific"


class Disclosure(str, Enum):
    yes = "yes"
    no = "no"
    prefer_not_to_say = "prefer_not_to_say"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Money(Model):
    min: int
    target: int

    @model_validator(mode="after")
    def _ordered(self) -> Money:
        if self.target < self.min:
            raise ValueError("expected_base_cad.target must be >= min")
        return self


class Identity(Model):
    name: str
    email: str
    phone: str
    city: str
    province: str = Field(pattern=r"^(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)$")
    links: dict[str, str] = Field(default_factory=dict)

    @property
    def location(self) -> str:
        """Canadian convention: `City, PROV` — never a full address."""
        return f"{self.city}, {self.province}"


class Constraints(Model):
    """Answers that stay constant across applications, plus the derived ones.

    `requires_sponsorship` is deliberately a property rather than a stored
    field. On an employer-specific permit, changing employers needs a new
    permit and often an LMIA, which is functionally what employers mean by
    sponsorship; answering "no" there surfaces at the offer stage. Deriving it
    from `permit_type` makes that impossible to get wrong by editing one line
    of YAML.
    """

    work_auth: str = "work_permit"
    permit_type: PermitType
    permit_subtype: str | None = None
    work_auth_expiry: dt.date | None = None
    citizenship: str
    credential_assessment: str | None = None
    provinces: list[str] = Field(default_factory=lambda: ["canada-wide"])
    work_modes: list[str] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    relocate_within_canada: bool = True
    expected_base_cad: Money | None = None
    notice_period_days: int = Field(default=0, ge=0)
    french_level: str = "none"

    @property
    def requires_sponsorship(self) -> bool:
        return self.permit_type is PermitType.employer_specific

    @property
    def show_work_auth_line(self) -> bool:
        """An open permit is worth stating; a closed one invites a screen-out."""
        return self.permit_type is PermitType.open

    def work_auth_line(self) -> str | None:
        if not self.show_work_auth_line:
            return None
        line = "Authorised to work in Canada"
        if self.permit_subtype:
            line += f" ({self.permit_subtype.upper()}"
            if self.work_auth_expiry:
                line += f", valid to {self.work_auth_expiry:%b %Y}"
            line += ")"
        elif self.work_auth_expiry:
            line += f" (open work permit, valid to {self.work_auth_expiry:%b %Y})"
        return line


class SelfId(Model):
    """Voluntary employment-equity answers, stored canonically.

    Adapters map these to each ATS's own option strings — Canadian
    employment-equity wording and US EEO-1 wording are not interchangeable.
    Nothing here is ever inferred from the rest of the profile.
    """

    mode: str = "prefer_not_to_say"
    gender: str = "prefer_not_to_say"
    indigenous: Disclosure = Disclosure.prefer_not_to_say
    racialized: Disclosure = Disclosure.prefer_not_to_say
    racialized_group: str | None = None
    disability: Disclosure = Disclosure.prefer_not_to_say
    veteran: Disclosure = Disclosure.prefer_not_to_say

    @field_validator("indigenous", "racialized", "disability", "veteran", mode="before")
    @classmethod
    def _yaml_booleans(cls, v: object) -> object:
        """YAML 1.1 parses bare `yes`/`no` as booleans. Accept both spellings."""
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v


class Role(Model):
    id: str
    title: str
    company: str
    location: str
    start: str = Field(pattern=YYYY_MM.pattern)
    end: str | None = None  # None means present

    @field_validator("end")
    @classmethod
    def _end_format(cls, v: str | None) -> str | None:
        if v is not None and not YYYY_MM.match(v):
            raise ValueError("end must be YYYY-MM or omitted for present")
        return v

    def date_range(self) -> str:
        def fmt(ym: str) -> str:
            return dt.datetime.strptime(ym, "%Y-%m").strftime("%b %Y")

        return f"{fmt(self.start)} – {fmt(self.end) if self.end else 'Present'}"


class Education(Model):
    institution: str
    credential: str
    location: str
    end: str = Field(pattern=YYYY_MM.pattern)
    start: str | None = None
    note: str | None = None

    def date_range(self) -> str:
        def fmt(ym: str) -> str:
            return dt.datetime.strptime(ym, "%Y-%m").strftime("%b %Y")

        return f"{fmt(self.start)} – {fmt(self.end)}" if self.start else fmt(self.end)


class Fact(Model):
    """One true thing you did, tagged for retrieval. The only legitimate source
    of resume content."""

    id: str = Field(pattern=r"^f_[a-z0-9_]+$")
    role: str
    bullet: str = Field(min_length=10)
    shapes: list[Shape] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    strength: int = Field(ge=1, le=5)


class Profile(Model):
    identity: Identity
    constraints: Constraints
    self_id: SelfId = Field(default_factory=SelfId)
    skills: dict[str, list[str]]
    skill_labels: dict[str, str] = Field(default_factory=dict)
    experience: list[Role] = Field(min_length=1)
    education: list[Education] = Field(default_factory=list)
    facts: list[Fact] = Field(min_length=1)
    summary: dict[Shape, str] = Field(default_factory=dict)

    @property
    def declared_skills(self) -> set[str]:
        return {s for group in self.skills.values() for s in group}

    def label(self, skill: str) -> str:
        """How a skill is written for humans.

        Skills are stored canonically and lowercased so that matching against a
        job description stays boring and reliable. Nobody wants `ci-cd` on their
        resume, though, so display is a separate concern: the canonical token is
        what gets verified, the label is only what gets printed.
        """
        return self.skill_labels.get(skill, skill)

    def role(self, role_id: str) -> Role:
        for r in self.experience:
            if r.id == role_id:
                return r
        raise KeyError(role_id)

    @model_validator(mode="after")
    def _cross_references(self) -> Profile:
        problems: list[str] = []

        seen: set[str] = set()
        for f in self.facts:
            if f.id in seen:
                problems.append(f"duplicate fact id: {f.id}")
            seen.add(f.id)

        role_ids = {r.id for r in self.experience}
        if len(role_ids) != len(self.experience):
            problems.append("duplicate role ids in experience")

        for f in self.facts:
            if f.role not in role_ids:
                problems.append(f"fact {f.id} references unknown role {f.role!r}")

        # Every skill a fact claims must be declared up front. This is what stops
        # a keyword drifting onto the resume via a bullet's tags.
        declared = self.declared_skills
        for f in self.facts:
            for s in f.skills:
                if s not in declared:
                    problems.append(
                        f"fact {f.id} claims skill {s!r}, which is not declared in `skills`"
                    )

        for s in self.skill_labels:
            if s not in declared:
                problems.append(f"skill_labels has {s!r}, which is not declared in `skills`")

        if problems:
            raise ValueError("; ".join(problems))
        return self
