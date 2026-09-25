"""Schema for `profile.yaml` — the fact bank every resume is built from.

Modelled on the source resume's own structure: a headline that changes with the
role, an experience section whose entries carry their own tech line, a projects
section that is first-class rather than an afterthought, and skills grouped the
way a reader expects to see them.

Nothing may reach a rendered resume that does not trace back to a `Fact` here.
`Profile` enforces the structural half; `verify.py` enforces the rest at render
time.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from resume.markup import strip_markup

YYYY_MM = r"^\d{4}-(0[1-9]|1[0-2])$"
YEAR_OR_MONTH = r"^\d{4}(-(0[1-9]|1[0-2]))?$"


class Shape(str, Enum):
    """The role shapes this history genuinely supports.

    Derived from the source resume rather than a generic taxonomy: the work is
    LLM product engineering, the Python service layer underneath it, and the
    Next.js/Supabase product surface around it. One shape per rendered resume.
    """

    ai_engineer = "ai_engineer"
    backend_python = "backend_python"
    fullstack = "fullstack"


class PermitType(str, Enum):
    open = "open"
    employer_specific = "employer_specific"


class Disclosure(str, Enum):
    yes = "yes"
    no = "no"
    prefer_not_to_say = "prefer_not_to_say"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Model):
    name: str
    email: str
    phone: str
    city: str
    province: str = Field(pattern=r"^(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)$")
    area: str | None = None  # e.g. "Toronto Area" — what a recruiter searches for
    street: str | None = None      # application forms ask; the resume never shows it
    postal_code: str | None = None
    country: str = "Canada"
    links: list[str] = Field(default_factory=list)

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def last_name(self) -> str:
        parts = self.name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    def link(self, kind: str) -> str | None:
        """The first link whose text mentions `kind` (linkedin, github, ...)."""
        for url in self.links:
            if kind in url.casefold():
                return url
        return None

    @property
    def location(self) -> str:
        base = f"{self.city}, {self.province}"
        return f"{base} ({self.area})" if self.area else base


class Constraints(Model):
    """Answers that stay constant across applications, plus the derived ones."""

    work_auth: str = "work_permit"
    permit_type: PermitType
    permit_subtype: str | None = None
    work_auth_expiry: dt.date | None = None
    citizenship: str
    credential_assessment: str | None = None
    provinces: list[str] = Field(default_factory=lambda: ["canada-wide"])
    work_modes: list[str] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    relocate_within_canada: bool = True
    expected_base_cad: str | None = None
    notice_period_days: int | None = None
    french_level: str = "none"

    @property
    def requires_sponsorship(self) -> bool:
        """Only an employer-specific permit needs a new one to change employer.

        Derived rather than stored so it cannot drift out of sync with reality
        by editing one line of YAML — answering this wrong surfaces at the offer
        stage, which is the worst possible moment.
        """
        return self.permit_type is PermitType.employer_specific

    @property
    def show_work_auth_line(self) -> bool:
        return self.permit_type is PermitType.open

    def work_auth_line(self) -> str | None:
        if not self.show_work_auth_line:
            return None
        line = "Authorised to work in Canada"
        detail = self.permit_subtype.upper() if self.permit_subtype else "open work permit"
        if self.work_auth_expiry:
            return f"{line} ({detail}, valid to {self.work_auth_expiry:%b %Y})"
        return f"{line} ({detail})"


class SelfId(Model):
    """Voluntary employment-equity answers, stored canonically.

    Adapters map these to each ATS's own option strings; Canadian
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


def _fmt(ym: str) -> str:
    if len(ym) == 4:
        return ym
    return dt.datetime.strptime(ym, "%Y-%m").strftime("%b %Y")


class Role(Model):
    id: str
    title: str
    company: str
    location: str
    start: str = Field(pattern=YYYY_MM)
    end: str | None = None
    tech: list[str] = Field(default_factory=list)

    @field_validator("end")
    @classmethod
    def _end_format(cls, v: str | None) -> str | None:
        if v is not None and not re.match(YYYY_MM, v):
            raise ValueError("end must be YYYY-MM, or omitted for present")
        return v

    def date_range(self) -> str:
        return f"{_fmt(self.start)} – {_fmt(self.end) if self.end else 'Present'}"


class Project(Model):
    """First-class, because for an early-career profile projects carry real weight."""

    id: str
    name: str
    tagline: str | None = None
    tech: list[str] = Field(default_factory=list)
    link: str | None = None


class Education(Model):
    credential: str
    institution: str
    location: str
    end: str = Field(pattern=YEAR_OR_MONTH)
    start: str | None = None
    note: str | None = None

    def date_range(self) -> str:
        return f"{_fmt(self.start)} – {_fmt(self.end)}" if self.start else _fmt(self.end)


class Fact(Model):
    """One true thing you did. The only legitimate source of resume content.

    `bullet` may use `**bold**`; every check runs on the stripped text.
    """

    id: str = Field(pattern=r"^f_[a-z0-9_]+$")
    role: str | None = None
    project: str | None = None
    bullet: str = Field(min_length=10)
    shapes: list[Shape] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    strength: int = Field(ge=1, le=5)

    @property
    def plain(self) -> str:
        return strip_markup(self.bullet)

    @property
    def anchor(self) -> str:
        """The role or project this bullet belongs under."""
        return self.role or self.project  # type: ignore[return-value]

    @model_validator(mode="after")
    def _one_anchor(self) -> Fact:
        if bool(self.role) == bool(self.project):
            raise ValueError(f"fact {self.id} must set exactly one of `role` or `project`")
        return self


class Profile(Model):
    identity: Identity
    constraints: Constraints
    self_id: SelfId = Field(default_factory=SelfId)
    headline: dict[Shape, str]
    summary: dict[Shape, str] = Field(default_factory=dict)
    skills: dict[str, list[str]]
    experience: list[Role] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    facts: list[Fact] = Field(min_length=1)
    answers: dict[str, str] = Field(default_factory=dict)

    def role(self, role_id: str) -> Role:
        for r in self.experience:
            if r.id == role_id:
                return r
        raise KeyError(role_id)

    def project(self, project_id: str) -> Project:
        for p in self.projects:
            if p.id == project_id:
                return p
        raise KeyError(project_id)

    @model_validator(mode="after")
    def _cross_references(self) -> Profile:
        problems: list[str] = []

        seen: set[str] = set()
        for f in self.facts:
            if f.id in seen:
                problems.append(f"duplicate fact id: {f.id}")
            seen.add(f.id)

        role_ids = {r.id for r in self.experience}
        project_ids = {p.id for p in self.projects}
        if len(role_ids) != len(self.experience):
            problems.append("duplicate role ids in experience")
        if len(project_ids) != len(self.projects):
            problems.append("duplicate project ids")
        if role_ids & project_ids:
            problems.append(f"ids shared between roles and projects: {sorted(role_ids & project_ids)}")

        for f in self.facts:
            if f.role and f.role not in role_ids:
                problems.append(f"fact {f.id} references unknown role {f.role!r}")
            if f.project and f.project not in project_ids:
                problems.append(f"fact {f.id} references unknown project {f.project!r}")

        for shape in Shape:
            if shape not in self.headline:
                problems.append(f"no headline for shape {shape.value!r}")

        if problems:
            raise ValueError("; ".join(problems))
        return self
