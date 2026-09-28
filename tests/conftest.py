from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

from resume.loader import ProfileLoader
from resume.schema import Profile

EXAMPLE = Path(__file__).resolve().parent.parent / "profile.example.yaml"


@pytest.fixture(autouse=True, scope="session")
def _example_is_the_fact_bank():
    """Point every default profile lookup at the committed example.

    `profile.yaml` is gitignored because it holds a real work history, so any
    test that reached the default path passed only on the author's machine and
    failed on a clean checkout. The example profile is committed and validates
    against the same schema, which is what these tests actually need.
    """
    previous = os.environ.get("JOB_TRACKER_PROFILE")
    os.environ["JOB_TRACKER_PROFILE"] = str(EXAMPLE)
    yield
    if previous is None:
        os.environ.pop("JOB_TRACKER_PROFILE", None)
    else:
        os.environ["JOB_TRACKER_PROFILE"] = previous


@pytest.fixture(scope="session")
def raw_example() -> dict:
    return yaml.load(EXAMPLE.read_text(encoding="utf-8"), Loader=ProfileLoader)


@pytest.fixture
def raw(raw_example) -> dict:
    """A fresh mutable copy, for building invalid profiles."""
    return copy.deepcopy(raw_example)


@pytest.fixture
def profile(raw_example) -> Profile:
    return Profile.model_validate(copy.deepcopy(raw_example))
