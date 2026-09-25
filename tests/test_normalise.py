"""Normalisation: when are two postings the same job?"""

from __future__ import annotations

import pytest

from api.normalise import (
    company_norm, dedupe_key, location_norm, remote_type, title_norm,
)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Shopify", "Shopify Inc."),
        ("Clio", "Clio Technologies Ltd"),
        ("Cohere", "cohere  "),
        ("Wealthsimple", "Wealthsimple Technologies Inc."),
    ],
)
def test_company_suffixes_collapse(a, b):
    assert company_norm(a) == company_norm(b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Sr. ML Engineer", "Senior Machine Learning Engineer"),
        ("Jr Python Dev", "Junior Python Developer"),
        ("Backend Engineer (Remote)", "Backend Engineer"),
        ("Software Engineer II", "Software Engineer"),
        ("SWE - Full Time", "Software Engineer"),
    ],
)
def test_title_variants_collapse(a, b):
    assert title_norm(a) == title_norm(b)


def test_genuinely_different_titles_stay_apart():
    assert title_norm("Data Engineer") != title_norm("Machine Learning Engineer")
    assert title_norm("Senior Backend Engineer") != title_norm("Backend Engineer")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Toronto", "toronto, on"),
        ("GTA", "toronto, on"),
        ("Greater Toronto Area", "toronto, on"),
        ("North York", "toronto, on"),
        ("Montréal", "montreal, qc"),
        ("Vancouver, British Columbia", "vancouver, bc"),
        ("Remote, Canada", "remote - canada"),
        ("Remote", "remote - canada"),
    ],
)
def test_locations_canonicalise(raw, expected):
    assert location_norm(raw) == expected


def test_remote_type_detection():
    assert remote_type("Remote, Canada") == "remote"
    assert remote_type("Toronto, ON", "This is a hybrid role") == "hybrid"
    assert remote_type("Toronto, ON", "On site five days") == "onsite"


def test_dedupe_key_ignores_location():
    """One requisition posted across four cities must stay one job.

    Keying on location would make it four rows, four tailored resumes, and
    four chances to apply twice to the same role.
    """
    a = dedupe_key("Shopify Inc.", "Sr. ML Engineer")
    b = dedupe_key("shopify", "Senior Machine Learning Engineer")
    assert a == b
    assert a != dedupe_key("Shopify", "Data Engineer")
    assert a != dedupe_key("Cohere", "Sr. ML Engineer")
