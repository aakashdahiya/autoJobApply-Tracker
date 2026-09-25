"""The fact bank's structural guarantees."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from resume.schema import PermitType, Profile, Shape


def test_example_profile_loads(profile):
    assert profile.facts
    assert profile.identity.location == "Ajax, ON (Toronto Area)"


def test_yaml_11_booleans_do_not_eat_values(profile):
    """`province: ON` and `disability: no` are strings, not booleans.

    PyYAML implements YAML 1.1, where both would otherwise parse as booleans —
    silently, and with real consequences on a form.
    """
    assert profile.identity.province == "ON"
    assert profile.self_id.disability.value == "no"
    assert profile.self_id.racialized.value == "yes"


def test_sponsorship_is_derived_not_stored(raw):
    """An open permit needs no sponsorship; a closed one does.

    Derived from `permit_type` so it cannot drift out of sync by editing one
    line of YAML — answering it wrong surfaces at the offer stage.
    """
    raw["constraints"]["permit_type"] = "open"
    assert Profile.model_validate(raw).constraints.requires_sponsorship is False

    raw["constraints"]["permit_type"] = "employer_specific"
    closed = Profile.model_validate(raw).constraints
    assert closed.requires_sponsorship is True
    assert closed.permit_type is PermitType.employer_specific


def test_work_auth_line_only_on_an_open_permit(raw):
    raw["constraints"]["permit_type"] = "open"
    line = Profile.model_validate(raw).constraints.work_auth_line()
    assert line is not None and "PGWP" in line

    raw["constraints"]["permit_type"] = "employer_specific"
    assert Profile.model_validate(raw).constraints.work_auth_line() is None


def test_fact_needs_exactly_one_anchor(raw):
    """A bullet belongs to one role or one project, never both or neither."""
    raw["facts"][0]["project"] = "p_example"
    with pytest.raises(ValidationError, match="exactly one"):
        Profile.model_validate(raw)

    del raw["facts"][0]["project"]
    del raw["facts"][0]["role"]
    with pytest.raises(ValidationError, match="exactly one"):
        Profile.model_validate(raw)


def test_duplicate_fact_id_rejected(raw):
    raw["facts"].append(dict(raw["facts"][0]))
    with pytest.raises(ValidationError, match="duplicate fact id"):
        Profile.model_validate(raw)


def test_fact_referencing_unknown_anchor_rejected(raw):
    raw["facts"][0]["role"] = "r_nonexistent"
    with pytest.raises(ValidationError, match="unknown role"):
        Profile.model_validate(raw)

    raw["facts"][0]["role"] = "r_example"
    raw["facts"][-1]["project"] = "p_nonexistent"
    with pytest.raises(ValidationError, match="unknown project"):
        Profile.model_validate(raw)


def test_every_shape_needs_a_headline(raw):
    """A resume without the right title line is not tailored, it is generic."""
    del raw["headline"]["ai_engineer"]
    with pytest.raises(ValidationError, match="no headline"):
        Profile.model_validate(raw)


def test_fact_needs_at_least_one_shape(raw):
    raw["facts"][0]["shapes"] = []
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_unknown_field_rejected(raw):
    """extra="forbid" everywhere, so a typo in a key fails loudly."""
    raw["identity"]["provice"] = "ON"
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_fact_plain_strips_markup(profile):
    bolded = [f for f in profile.facts if "**" in f.bullet]
    assert bolded, "fixture should contain bolded bullets"
    for f in bolded:
        assert "**" not in f.plain


def test_shapes_cover_the_real_history():
    assert {s.value for s in Shape} == {"ai_engineer", "backend_python", "fullstack"}
