"""The fact bank's structural guarantees."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from resume.schema import PermitType, Profile


def test_example_profile_loads(profile):
    assert profile.facts
    assert profile.identity.location == "Toronto, ON"


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

    This is derived from `permit_type` precisely so it cannot drift out of sync
    with reality by editing one line of YAML.
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


def test_duplicate_fact_id_rejected(raw):
    raw["facts"].append(dict(raw["facts"][0]))
    with pytest.raises(ValidationError, match="duplicate fact id"):
        Profile.model_validate(raw)


def test_fact_referencing_unknown_role_rejected(raw):
    raw["facts"][0]["role"] = "r_nonexistent"
    with pytest.raises(ValidationError, match="unknown role"):
        Profile.model_validate(raw)


def test_fact_claiming_undeclared_skill_rejected(raw):
    """A keyword cannot reach the resume by hiding in a bullet's tags."""
    raw["facts"][0]["skills"].append("rust")
    with pytest.raises(ValidationError, match="not declared"):
        Profile.model_validate(raw)


def test_fact_needs_at_least_one_shape(raw):
    raw["facts"][0]["shapes"] = []
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_strength_is_bounded(raw):
    raw["facts"][0]["strength"] = 9
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_unknown_field_rejected(raw):
    """extra="forbid" everywhere, so a typo in a key fails loudly."""
    raw["identity"]["provice"] = "ON"
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_skill_label_for_undeclared_skill_rejected(raw):
    raw["skill_labels"]["rust"] = "Rust"
    with pytest.raises(ValidationError, match="skill_labels"):
        Profile.model_validate(raw)
