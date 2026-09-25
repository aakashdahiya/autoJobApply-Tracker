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
    selection = select(profile, Shape.fullstack)
    entry = selection.entries[0]
    entry.bullets.append(
        SelectedBullet(
            fact_id="f_invented",
            text="Led a team of 40 engineers across three continents.",
            anchor_id=entry.obj.id,
        )
    )
    assert "untraceable_bullet" in kinds(check_selection(profile, selection))


def test_invented_number_is_caught_even_when_rephrasing_is_allowed(profile):
    """The number check is what survives into Phase 3 tailoring.

    Rewording is licensed; inventing a 78% improvement no source fact states
    is not.
    """
    selection = select(profile, Shape.fullstack)
    entry = selection.entries[0]
    entry.bullets[0] = replace(entry.bullets[0], text=entry.bullets[0].text + " A 78% gain.")

    problems = check_selection(profile, selection, strict=False)
    assert "invented_number" in kinds(problems)
    assert any("78" in p.detail for p in problems)


def test_markup_cannot_hide_an_invented_number(profile):
    """Bolding a fabricated figure must not exempt it from the check."""
    selection = select(profile, Shape.fullstack)
    entry = selection.entries[0]
    entry.bullets[0] = replace(entry.bullets[0], text=entry.bullets[0].text + " **A 78% gain.**")
    assert "invented_number" in kinds(check_selection(profile, selection, strict=False))


def test_rephrasing_is_rejected_in_strict_mode_and_allowed_otherwise(profile):
    """Phase 0 renders source text verbatim; Phase 3 relaxes exactly this check."""
    selection = select(profile, Shape.fullstack)
    entry = selection.entries[0]
    entry.bullets[0] = replace(
        entry.bullets[0], text=entry.bullets[0].text.replace("Increased", "Raised")
    )

    assert "rephrased_bullet" in kinds(check_selection(profile, selection, strict=True))
    assert check_selection(profile, selection, strict=False) == []


def test_misattributed_bullet_is_caught(profile):
    """A real achievement under the wrong employer is still a false claim."""
    selection = select(profile, Shape.fullstack)
    role_entry, project_entry = selection.experience[0], selection.projects[0]
    role_entry.bullets[0] = replace(role_entry.bullets[0], anchor_id=project_entry.obj.id)
    assert "misattributed_bullet" in kinds(check_selection(profile, selection))
