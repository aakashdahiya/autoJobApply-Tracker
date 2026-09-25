"""Pick which facts go on the resume for one shape, and report per-shape depth.

Selection is deliberately dumb and deterministic: rank by strength, give every
entry that has something relevant a voice, respect a page budget. The
interesting judgement arrives in Phase 3, when a real job description narrows
the shape and the keyword targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from resume.markup import strip_markup
from resume.schema import Fact, Profile, Project, Role, Shape

MAX_BULLETS_TOTAL = 14
MAX_BULLETS_PER_ENTRY = 6

# Below these, a shape cannot fill a resume without padding. For an
# early-career profile that is a signal to build another project, not a reason
# to lower the bar.
MIN_FACTS_PER_SHAPE = 6
MIN_STRONG_FACTS_PER_SHAPE = 3
STRONG = 4


@dataclass(frozen=True)
class SelectedBullet:
    """A bullet on its way to the page, still carrying its provenance."""

    fact_id: str
    text: str  # may contain **bold** markup
    anchor_id: str

    @property
    def plain(self) -> str:
        return strip_markup(self.text)


@dataclass
class Entry:
    kind: Literal["role", "project"]
    obj: Role | Project
    bullets: list[SelectedBullet]


@dataclass
class Selection:
    shape: Shape
    headline: str
    summary: str | None
    experience: list[Entry] = field(default_factory=list)
    projects: list[Entry] = field(default_factory=list)
    skills: dict[str, list[str]] = field(default_factory=dict)

    @property
    def entries(self) -> list[Entry]:
        return self.experience + self.projects

    @property
    def bullets(self) -> list[SelectedBullet]:
        return [b for e in self.entries for b in e.bullets]


@dataclass
class ShapeDepth:
    shape: Shape
    total: int
    strong: int
    entries_covered: int
    ok: bool
    message: str


def _eligible(profile: Profile, shape: Shape) -> list[Fact]:
    return [f for f in profile.facts if shape in f.shapes]


def _roles_newest_first(profile: Profile) -> list[Role]:
    """Reverse chronological — the only ordering an ATS reliably understands."""
    return sorted(
        profile.experience, key=lambda r: (r.end or "9999-99", r.start), reverse=True
    )


def select(profile: Profile, shape: Shape) -> Selection:
    """Choose bullets for one shape, across experience and projects."""
    by_anchor: dict[str, list[Fact]] = {}
    for f in _eligible(profile, shape):
        by_anchor.setdefault(f.anchor, []).append(f)
    for facts in by_anchor.values():
        facts.sort(key=lambda f: -f.strength)  # stable, so file order breaks ties

    ordered: list[tuple[Literal["role", "project"], Role | Project]] = [
        ("role", r) for r in _roles_newest_first(profile) if by_anchor.get(r.id)
    ]
    ordered += [("project", p) for p in profile.projects if by_anchor.get(p.id)]

    chosen: dict[str, list[Fact]] = {obj.id: [] for _kind, obj in ordered}

    # Every entry with something relevant gets one bullet before any entry gets
    # a second, so a strong role cannot crowd the projects off the page.
    budget = MAX_BULLETS_TOTAL
    for depth in range(MAX_BULLETS_PER_ENTRY):
        for _kind, obj in ordered:
            if budget == 0:
                break
            pool = by_anchor[obj.id]
            if depth < len(pool):
                chosen[obj.id].append(pool[depth])
                budget -= 1
        if budget == 0:
            break

    selection = Selection(
        shape=shape,
        headline=profile.headline[shape],
        summary=profile.summary.get(shape),
        skills={category: list(items) for category, items in profile.skills.items()},
    )
    for kind, obj in ordered:
        bullets = [
            SelectedBullet(fact_id=f.id, text=f.bullet, anchor_id=obj.id)
            for f in chosen[obj.id]
        ]
        if not bullets:
            continue
        entry = Entry(kind=kind, obj=obj, bullets=bullets)
        (selection.experience if kind == "role" else selection.projects).append(entry)

    return selection


def skills_without_evidence(profile: Profile, selection: Selection) -> list[str]:
    """Advisory: skills printed that no selected bullet visibly demonstrates.

    Not an error. A resume legitimately lists tools that no bullet has room to
    mention, and coursework is real evidence too. But it is worth knowing which
    claims an interviewer could ask about and find nothing on the page for.
    """
    fact_by_id = {f.id: f for f in profile.facts}
    shown = " ".join(b.plain for b in selection.bullets).casefold()
    tagged = {
        s.casefold()
        for b in selection.bullets
        if b.fact_id in fact_by_id
        for s in fact_by_id[b.fact_id].skills
    }
    missing: list[str] = []
    for items in selection.skills.values():
        for skill in items:
            token = skill.split("(")[0].strip().casefold()
            if token and token not in shown and token not in tagged:
                missing.append(skill)
    return missing


def depth_report(profile: Profile) -> list[ShapeDepth]:
    """Is each shape deep enough to tailor for credibly?"""
    report: list[ShapeDepth] = []
    for shape in Shape:
        facts = _eligible(profile, shape)
        strong = [f for f in facts if f.strength >= STRONG]
        anchors = {f.anchor for f in facts}
        ok = len(facts) >= MIN_FACTS_PER_SHAPE and len(strong) >= MIN_STRONG_FACTS_PER_SHAPE
        if ok:
            message = f"{len(facts)} facts ({len(strong)} strong) across {len(anchors)} entries"
        else:
            missing = []
            if len(facts) < MIN_FACTS_PER_SHAPE:
                missing.append(f"{MIN_FACTS_PER_SHAPE - len(facts)} more facts")
            if len(strong) < MIN_STRONG_FACTS_PER_SHAPE:
                missing.append(f"{MIN_STRONG_FACTS_PER_SHAPE - len(strong)} more strong facts")
            message = (
                f"thin: {len(facts)} facts ({len(strong)} strong) — wants " + ", ".join(missing)
            )
        report.append(
            ShapeDepth(
                shape=shape,
                total=len(facts),
                strong=len(strong),
                entries_covered=len(anchors),
                ok=ok,
                message=message,
            )
        )
    return report
