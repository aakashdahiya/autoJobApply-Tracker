"""Selection: one shape per resume, every entry heard, budget respected."""

from __future__ import annotations

from resume.schema import Profile, Shape
from resume.select import (
    MAX_BULLETS_PER_ENTRY,
    MAX_BULLETS_TOTAL,
    depth_report,
    select,
    skills_without_evidence,
)


def test_only_facts_tagged_for_the_shape_are_used(profile):
    """The whole point of shapes: an AI resume shows the AI facts."""
    fact_by_id = {f.id: f for f in profile.facts}
    for shape in Shape:
        selection = select(profile, shape)
        assert selection.bullets, shape
        for bullet in selection.bullets:
            assert shape in fact_by_id[bullet.fact_id].shapes


def test_headline_and_summary_follow_the_shape(profile):
    for shape in Shape:
        selection = select(profile, shape)
        assert selection.headline == profile.headline[shape]
        assert selection.summary == profile.summary.get(shape)


def test_budget_respected(profile):
    for shape in Shape:
        selection = select(profile, shape)
        assert len(selection.bullets) <= MAX_BULLETS_TOTAL
        for entry in selection.entries:
            assert len(entry.bullets) <= MAX_BULLETS_PER_ENTRY


def test_every_entry_with_relevant_facts_gets_a_voice(profile):
    """A strong role must not crowd the projects off the page."""
    for shape in Shape:
        selection = select(profile, shape)
        eligible = {f.anchor for f in profile.facts if shape in f.shapes}
        assert {e.obj.id for e in selection.entries} == eligible


def test_experience_precedes_projects_and_roles_are_newest_first(profile):
    selection = select(profile, Shape.fullstack)
    assert all(e.kind == "role" for e in selection.experience)
    assert all(e.kind == "project" for e in selection.projects)
    ends = [e.obj.end or "9999-99" for e in selection.experience]
    assert ends == sorted(ends, reverse=True)


def test_bullets_keep_their_provenance(profile):
    fact_by_id = {f.id: f for f in profile.facts}
    for bullet in select(profile, Shape.ai_engineer).bullets:
        source = fact_by_id[bullet.fact_id]
        assert bullet.text == source.bullet
        assert bullet.anchor_id == source.anchor


def test_depth_report_flags_a_thin_shape(raw):
    """Breadth is only free if it is real — a thin shape must say so."""
    for fact in raw["facts"]:
        fact["shapes"] = [s for s in fact["shapes"] if s != "ai_engineer"] or ["fullstack"]
    raw["facts"][0]["shapes"] = list({*raw["facts"][0]["shapes"], "ai_engineer"})
    profile = Profile.model_validate(raw)

    report = {d.shape: d for d in depth_report(profile)}
    assert report[Shape.ai_engineer].ok is False
    assert "thin" in report[Shape.ai_engineer].message


def test_skills_without_evidence_is_advisory_not_a_gate(profile):
    """A resume may list a tool no bullet has room to mention; it should just say so."""
    selection = select(profile, Shape.ai_engineer)
    missing = skills_without_evidence(profile, selection)
    assert isinstance(missing, list)
    printed = {s for items in selection.skills.values() for s in items}
    assert set(missing) <= printed
