"""Selection: one shape per resume, every role heard, budget respected."""

from __future__ import annotations

from resume.schema import Profile, Shape
from resume.select import (
    MAX_BULLETS_PER_ROLE,
    MAX_BULLETS_TOTAL,
    depth_report,
    select,
)


def test_only_facts_tagged_for_the_shape_are_used(profile):
    """The whole point of shapes: a research resume shows research facts."""
    fact_by_id = {f.id: f for f in profile.facts}
    for shape in Shape:
        selection = select(profile, shape)
        assert selection.bullets, shape
        for bullet in selection.bullets:
            assert shape in fact_by_id[bullet.fact_id].shapes


def test_budget_respected(profile):
    for shape in Shape:
        selection = select(profile, shape)
        assert len(selection.bullets) <= MAX_BULLETS_TOTAL
        for _role, bullets in selection.roles:
            assert len(bullets) <= MAX_BULLETS_PER_ROLE


def test_every_role_with_relevant_facts_gets_a_voice(profile):
    """Recent history must not be silently dropped by a strength ranking."""
    for shape in Shape:
        selection = select(profile, shape)
        eligible_roles = {f.role for f in profile.facts if shape in f.shapes}
        represented = {role.id for role, _ in selection.roles}
        assert represented == eligible_roles


def test_roles_are_reverse_chronological(profile):
    selection = select(profile, Shape.ml_platform)
    ends = [role.end or "9999-99" for role, _ in selection.roles]
    assert ends == sorted(ends, reverse=True)


def test_skills_section_is_derived_from_selected_facts(profile):
    """No skill on the page that no bullet on the page supports."""
    fact_by_id = {f.id: f for f in profile.facts}
    selection = select(profile, Shape.research)
    supported = {s for b in selection.bullets for s in fact_by_id[b.fact_id].skills}
    on_page = {s for group in selection.skills.values() for s in group}
    assert on_page <= supported
    assert on_page <= profile.declared_skills


def test_bullets_keep_their_provenance(profile):
    fact_by_id = {f.id: f for f in profile.facts}
    for bullet in select(profile, Shape.ml_platform).bullets:
        assert bullet.text == fact_by_id[bullet.fact_id].bullet
        assert bullet.role_id == fact_by_id[bullet.fact_id].role


def test_depth_report_flags_a_thin_shape(raw):
    """Breadth is only free if it is real — a thin shape must say so."""
    for fact in raw["facts"]:
        if "research" in fact["shapes"]:
            fact["shapes"] = [s for s in fact["shapes"] if s != "research"]
    raw["facts"][0]["shapes"].append("research")
    profile = Profile.model_validate(raw)

    report = {d.shape: d for d in depth_report(profile)}
    assert report[Shape.research].ok is False
    assert "thin" in report[Shape.research].message
    assert report[Shape.ml_platform].ok is True


def test_depth_report_passes_on_the_example(profile):
    assert all(d.ok for d in depth_report(profile))
