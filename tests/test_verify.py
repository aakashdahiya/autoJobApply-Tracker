"""The fabrication guards. Each test is a way the system could lie."""

from __future__ import annotations

from dataclasses import replace

from resume.schema import Shape
from resume.select import SelectedBullet, select
from resume.verify import check_selection, extract_numbers


def kinds(problems) -> set[str]:
    return {p.kind for p in problems}


def test_clean_selection_has_no_problems(profile):
    for shape in Shape:
        assert check_selection(profile, select(profile, shape)) == []


def test_extract_numbers_normalises_commas():
    assert extract_numbers("1,200 requests at 99.9% over 3 days") == {"1200", "99.9", "3"}
    assert extract_numbers("no digits here") == set()


def test_invented_bullet_is_caught(profile):
    """A bullet with no fact behind it cannot reach the page."""
    selection = select(profile, Shape.ml_platform)
    role, bullets = selection.roles[0]
    bullets.append(
        SelectedBullet(
            fact_id="f_invented",
            text="Led a team of 40 engineers across three continents.",
            role_id=role.id,
        )
    )
    assert "untraceable_bullet" in kinds(check_selection(profile, selection))


def test_invented_number_is_caught_even_when_rephrasing_is_allowed(profile):
    """The number check is what survives into Phase 3 tailoring.

    Rewording is licensed; inventing a 78% improvement that no source fact
    states is not.
    """
    selection = select(profile, Shape.ml_platform)
    role, bullets = selection.roles[0]
    bullets[0] = replace(bullets[0], text=bullets[0].text + " A 78% improvement.")

    problems = check_selection(profile, selection, strict=False)
    assert "invented_number" in kinds(problems)
    assert any("78" in p.detail for p in problems)


def test_rephrasing_is_rejected_in_strict_mode_and_allowed_otherwise(profile):
    """Phase 0 renders source text verbatim; Phase 3 relaxes exactly this one check."""
    selection = select(profile, Shape.ml_platform)
    _role, bullets = selection.roles[0]
    original = bullets[0].text
    bullets[0] = replace(bullets[0], text=original.replace("Cut", "Reduced"))

    assert "rephrased_bullet" in kinds(check_selection(profile, selection, strict=True))
    assert check_selection(profile, selection, strict=False) == []


def test_undeclared_skill_is_caught(profile):
    selection = select(profile, Shape.ml_platform)
    selection.skills["Languages"] = ["rust"]
    assert "undeclared_skill" in kinds(check_selection(profile, selection))


def test_skill_no_bullet_supports_is_caught(profile):
    """Declared is not enough — some bullet on the page must back it up."""
    selection = select(profile, Shape.ml_platform)
    fact_by_id = {f.id: f for f in profile.facts}
    supported = {s for b in selection.bullets for s in fact_by_id[b.fact_id].skills}
    unsupported = sorted(profile.declared_skills - supported)
    assert unsupported, "fixture should leave at least one declared-but-unused skill"

    selection.skills.setdefault("Practices", []).append(unsupported[0])
    assert "unsupported_skill" in kinds(check_selection(profile, selection))


def test_misattributed_bullet_is_caught(profile):
    """A real achievement under the wrong employer is still a false claim."""
    selection = select(profile, Shape.ml_platform)
    role_a, bullets_a = selection.roles[0]
    role_b, _ = selection.roles[1]
    bullets_a[0] = replace(bullets_a[0], role_id=role_b.id)
    assert "misattributed_bullet" in kinds(check_selection(profile, selection))
