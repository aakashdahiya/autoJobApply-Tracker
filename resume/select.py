"""Pick which facts go on the resume for one shape, and report per-shape depth.

Selection is deliberately dumb and deterministic: rank by strength, guarantee
every role that has something relevant gets a voice, respect a page budget.
The interesting judgement happens later, when a real job description narrows
the shape and the keyword targets (Phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from resume.schema import Fact, Profile, Role, Shape

# A two-page Canadian resume comfortably carries this much. Canadian convention
# accepts two pages, so the budget is not the one-page US default.
MAX_BULLETS_TOTAL = 14
MAX_BULLETS_PER_ROLE = 5

# Below these, tailoring for a shape produces thin output. Warn at build time
# rather than discovering it when a posting needs that shape.
MIN_FACTS_PER_SHAPE = 8
MIN_STRONG_FACTS_PER_SHAPE = 4
STRONG = 4


@dataclass(frozen=True)
class SelectedBullet:
    """A bullet on its way to the page, still carrying its provenance."""

    fact_id: str
    text: str
    role_id: str


@dataclass
class Selection:
    shape: Shape
    summary: str | None
    roles: list[tuple[Role, list[SelectedBullet]]] = field(default_factory=list)
    skills: dict[str, list[str]] = field(default_factory=dict)

    @property
    def bullets(self) -> list[SelectedBullet]:
        return [b for _, bs in self.roles for b in bs]


@dataclass
class ShapeDepth:
    shape: Shape
    total: int
    strong: int
    roles_covered: int
    ok: bool
    message: str


def _eligible(profile: Profile, shape: Shape) -> list[Fact]:
    return [f for f in profile.facts if shape in f.shapes]


def _role_order(profile: Profile) -> list[Role]:
    """Reverse chronological, the only ordering an ATS reliably understands."""
    return sorted(profile.experience, key=lambda r: (r.end or "9999-99", r.start), reverse=True)


def select(profile: Profile, shape: Shape) -> Selection:
    """Choose bullets and skills for one shape."""
    eligible = _eligible(profile, shape)
    by_role: dict[str, list[Fact]] = {}
    for f in eligible:
        by_role.setdefault(f.role, []).append(f)
    # Strongest first, stable within a strength so the file order breaks ties.
    for facts in by_role.values():
        facts.sort(key=lambda f: -f.strength)

    ordered_roles = [r for r in _role_order(profile) if by_role.get(r.id)]
    chosen: dict[str, list[Fact]] = {r.id: [] for r in ordered_roles}

    # Every role that has something relevant gets one bullet before any role
    # gets a second, so recent history is never silently dropped.
    budget = MAX_BULLETS_TOTAL
    for depth in range(MAX_BULLETS_PER_ROLE):
        for role in ordered_roles:
            if budget == 0:
                break
            pool = by_role[role.id]
            if depth < len(pool):
                chosen[role.id].append(pool[depth])
                budget -= 1
        if budget == 0:
            break

    selection = Selection(shape=shape, summary=profile.summary.get(shape))
    for role in ordered_roles:
        bullets = [
            SelectedBullet(fact_id=f.id, text=f.bullet, role_id=role.id)
            for f in chosen[role.id]
        ]
        if bullets:
            selection.roles.append((role, bullets))

    selection.skills = _skills_for(profile, selection)
    return selection


def _skills_for(profile: Profile, selection: Selection) -> dict[str, list[str]]:
    """Derive the skills section from the facts actually on the page.

    This makes the skills section traceable for free: it cannot contain a
    keyword that no selected bullet supports, and it stays relevant to the
    shape without a second list to maintain.
    """
    fact_by_id = {f.id: f for f in profile.facts}
    supported = {s for b in selection.bullets for s in fact_by_id[b.fact_id].skills}
    out: dict[str, list[str]] = {}
    for category, skills in profile.skills.items():
        kept = [s for s in skills if s in supported]  # declared order preserved
        if kept:
            out[category] = kept
    return out


def depth_report(profile: Profile) -> list[ShapeDepth]:
    """Is each shape deep enough to tailor for credibly?"""
    report: list[ShapeDepth] = []
    for shape in Shape:
        facts = _eligible(profile, shape)
        strong = [f for f in facts if f.strength >= STRONG]
        roles = {f.role for f in facts}
        ok = len(facts) >= MIN_FACTS_PER_SHAPE and len(strong) >= MIN_STRONG_FACTS_PER_SHAPE
        if ok:
            message = f"{len(facts)} facts ({len(strong)} strong) across {len(roles)} roles"
        else:
            missing = []
            if len(facts) < MIN_FACTS_PER_SHAPE:
                missing.append(f"needs {MIN_FACTS_PER_SHAPE - len(facts)} more facts")
            if len(strong) < MIN_STRONG_FACTS_PER_SHAPE:
                missing.append(
                    f"needs {MIN_STRONG_FACTS_PER_SHAPE - len(strong)} more strength-{STRONG}+ facts"
                )
            message = f"thin: {len(facts)} facts ({len(strong)} strong) — " + ", ".join(missing)
        report.append(
            ShapeDepth(
                shape=shape,
                total=len(facts),
                strong=len(strong),
                roles_covered=len(roles),
                ok=ok,
                message=message,
            )
        )
    return report
