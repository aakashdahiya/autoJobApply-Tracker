from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from resume.loader import ProfileLoader
from resume.schema import Profile

EXAMPLE = Path(__file__).resolve().parent.parent / "profile.example.yaml"


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
