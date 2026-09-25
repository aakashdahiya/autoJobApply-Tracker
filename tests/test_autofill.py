"""What goes in each form field — and, more importantly, what does not."""

from __future__ import annotations

import pytest

from api.autofill import FieldSpec, choose_option, resolve_field
from resume.schema import Profile


@pytest.fixture
def prof(profile):
    return profile


def fill(profile, label, type="text", options=(), automation_id=""):
    return resolve_field(
        profile,
        FieldSpec("x", label, type, tuple(options), automation_id=automation_id),
    )


# --- the option matcher -----------------------------------------------------

def test_choose_option_prefers_exact_then_whole_words():
    assert choose_option(("Yes", "No"), "yes") == "Yes"
    assert choose_option(("Yes, I am legally authorized", "No"), "yes") == (
        "Yes, I am legally authorized"
    )


def test_choose_option_will_not_match_inside_a_word():
    """"no" lives inside "visible minority".

    A loose substring check answers "No" to "are you a member of a visible
    minority?", which is both wrong and the kind of wrong that stops you
    trusting the fill.
    """
    assert choose_option(("Yes", "No", "Prefer not to answer"), "visible minority") is None
    assert choose_option(("Yes", "No"), "minority") is None


def test_choose_option_returns_none_rather_than_guessing():
    assert choose_option(("Alpha", "Beta"), "gamma") is None
    assert choose_option((), "yes") is None


# --- identity ---------------------------------------------------------------

def test_name_fields(prof):
    assert fill(prof, "First Name").value == prof.identity.first_name
    assert fill(prof, "Last Name").value == prof.identity.last_name
    assert fill(prof, "Full Name").value == prof.identity.name


def test_narrow_patterns_beat_the_broad_name_rule(prof):
    """"First Name" must not be caught by the bare "name" rule."""
    assert fill(prof, "First Name").value != prof.identity.name


def test_contact_and_links(prof):
    assert "@" in fill(prof, "Email").value
    assert fill(prof, "Phone").value == prof.identity.phone
    assert "linkedin" in fill(prof, "LinkedIn Profile").value
    assert "github" in fill(prof, "GitHub URL").value


def test_city_and_country(prof):
    assert fill(prof, "City").value == prof.identity.city
    assert fill(prof, "Country", "select", ["Canada", "United States"]).value == "Canada"


def test_unstored_fields_are_not_invented(prof):
    """No postal code in the profile means no postal code on the form."""
    assert fill(prof, "Postal Code").action == "unmatched"


# --- work authorisation -----------------------------------------------------

def test_entitled_to_work_is_yes(prof):
    assert fill(prof, "Are you legally entitled to work in Canada?", "select",
                ["Yes", "No"]).value == "Yes"


def test_sponsorship_answer_follows_permit_type(prof, raw):
    assert fill(prof, "Will you now or in the future require sponsorship?", "select",
                ["Yes", "No"]).value == "No"

    raw["constraints"]["permit_type"] = "employer_specific"
    closed = Profile.model_validate(raw)
    assert fill(closed, "Will you require visa sponsorship?", "select",
                ["Yes", "No"]).value == "Yes"


def test_verbose_option_wording_is_matched(prof):
    assert fill(prof, "Are you legally authorized to work?", "select",
                ["Yes, I am legally authorized", "No, I require sponsorship"]).value == (
        "Yes, I am legally authorized"
    )


# --- logistics --------------------------------------------------------------

def test_notice_and_salary(prof):
    assert fill(prof, "Notice period").value == "Immediately"
    assert fill(prof, "Expected salary").value == prof.constraints.expected_base_cad


def test_relocation(prof):
    assert fill(prof, "Are you willing to relocate?", "select", ["Yes", "No"]).value == "Yes"


# --- self-identification ----------------------------------------------------

def test_canadian_and_us_wording_from_one_canonical_value(prof):
    """The same profile answers both forms, in each form's own words."""
    assert fill(prof, "Are you a member of a visible minority?", "select",
                ["Yes", "No", "Prefer not to answer"]).value == "Yes"
    assert fill(prof, "Race/Ethnicity", "select",
                ["White", "Black or African American", "Asian", "Hispanic or Latino"]).value == (
        "Asian"
    )


def test_disability_and_veteran_use_the_page_wording(prof):
    assert fill(prof, "Do you have a disability?", "select",
                ["Yes, I have a disability", "No, I do not have a disability"]).value == (
        "No, I do not have a disability"
    )
    assert fill(prof, "Protected veteran status", "select",
                ["I am a protected veteran", "I am not a protected veteran"]).value == (
        "I am not a protected veteran"
    )


def test_nothing_is_disclosed_when_the_profile_says_do_not(raw):
    raw["self_id"]["mode"] = "prefer_not_to_say"
    quiet = Profile.model_validate(raw)
    for label in ("Gender", "Are you a member of a visible minority?", "Disability status"):
        result = fill(quiet, label, "select", ["Yes", "No"])
        assert result.action == "skip"
        assert "voluntary" in result.reason


def test_an_option_set_that_does_not_fit_is_skipped_not_forced(prof):
    result = fill(prof, "Gender", "select", ["Alpha", "Beta"])
    assert result.action == "skip"


# --- things it must never do ------------------------------------------------

def test_motivation_text_is_never_generated(prof):
    for label in ("Cover letter", "Tell us about a time you failed",
                  "What interests you about this role?"):
        result = fill(prof, label, "textarea")
        assert result.action == "skip"
        assert "yourself" in result.reason


def test_an_explicit_bank_answer_outranks_the_motivation_rule(prof, raw):
    """If you wrote the answer yourself, it is yours to use."""
    raw["answers"]["why do you want to work here"] = "Because of the product."
    explicit = Profile.model_validate(raw)
    assert fill(explicit, "Why do you want to work here?", "textarea").value == (
        "Because of the product."
    )


def test_passwords_are_never_autofilled(prof):
    assert fill(prof, "Password", "password").action == "skip"


def test_salary_history_is_declined(prof):
    result = fill(prof, "Current salary")
    assert result.action == "skip"
    assert "provinces" in result.reason


def test_resume_upload_is_flagged_for_attachment(prof):
    assert fill(prof, "Resume/CV", "file").action == "skip"


def test_unknown_labels_are_reported_not_guessed(prof):
    result = fill(prof, "Favourite colour")
    assert result.action == "unmatched"
    assert result.value is None


# --- the answer bank --------------------------------------------------------

def test_answer_bank_handles_what_no_rule_can(prof):
    assert fill(prof, "How did you hear about us?").value == "Company careers page"


def test_a_blank_bank_answer_means_leave_it_blank(prof):
    assert fill(prof, "Why do you want to work here").action == "skip"


# --- workday specifics ------------------------------------------------------

def test_workday_automation_ids_are_matched_when_the_label_is_empty(prof):
    """Workday often renders its label out of reach; the automation id carries it."""
    result = fill(prof, "", "text", automation_id="legalNameSection_firstName")
    assert result.value == prof.identity.first_name
