"""Load and validate `profile.yaml`."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from resume.schema import Profile

_BOOL_ONLY = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class ProfileLoader(yaml.SafeLoader):
    """A YAML loader without YAML 1.1's surprise booleans.

    PyYAML implements YAML 1.1, where bare `ON`, `OFF`, `YES`, `NO`, `Y` and `N`
    are all booleans. In this schema that is actively dangerous: `province: ON`
    silently becomes `True`, and `disability: no` becomes `False` on its way to
    a form field where the difference matters. Only `true`/`false` remain
    boolean here, so provinces and yes/no answers survive as written.
    """


ProfileLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ProfileLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL_ONLY, list("tTfF"))


def load_profile(path: str | Path) -> Profile:
    """Parse and validate a profile file.

    Raises `pydantic.ValidationError` listing every problem at once, so a
    malformed fact bank is fixed in one pass rather than one error per run.
    """
    raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=ProfileLoader)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return Profile.model_validate(raw)
