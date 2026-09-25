"""The checks that make the no-fabrication rule mechanical.

Two layers:

* `check_selection` runs before rendering. Every bullet must trace to a real
  fact, and no number may appear that the source fact does not contain. In
  Phase 0 nothing rephrases, so `strict` also demands character-identical
  text; Phase 3 turns `strict` off when constrained rephrasing arrives, and
  the number and skill checks keep doing their job unchanged.

* `verify_pdf` runs after rendering. It pulls the text back out of the
  generated PDF and asserts that every bullet survived, in order, with the
  contact details present. This is the one-line test that catches the ATS
  parsing disasters people only discover from silence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pdfminer.high_level import extract_text

from resume.schema import Profile
from resume.select import Selection

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}


@dataclass(frozen=True)
class Problem:
    kind: str
    detail: str
    fact_id: str | None = None

    def __str__(self) -> str:
        where = f" [{self.fact_id}]" if self.fact_id else ""
        return f"{self.kind}{where}: {self.detail}"


def extract_numbers(text: str) -> set[str]:
    """Numeric claims in a string, comma-normalised so `1,200` == `1200`."""
    return {m.group().replace(",", "") for m in NUMBER.finditer(text)}


def normalise(text: str) -> str:
    """Flatten a string for comparison against PDF-extracted text.

    PDF extraction reflows lines, resolves ligatures inconsistently, and
    renders dashes in whatever form the font supplies, so none of that can be
    allowed to fail a comparison about content.
    """
    text = unicodedata.normalize("NFKC", text)
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    text = text.replace("–", "-").replace("—", "-").replace("→", "->")
    return re.sub(r"\s+", " ", text).strip().casefold()


def check_selection(profile: Profile, selection: Selection, *, strict: bool = True) -> list[Problem]:
    """Validate a selection against the fact bank before it is rendered."""
    problems: list[Problem] = []
    fact_by_id = {f.id: f for f in profile.facts}

    for bullet in selection.bullets:
        source = fact_by_id.get(bullet.fact_id)
        if source is None:
            problems.append(
                Problem("untraceable_bullet", "no such fact in the profile", bullet.fact_id)
            )
            continue

        if strict and bullet.text != source.bullet:
            problems.append(
                Problem("rephrased_bullet", "text differs from its source fact", bullet.fact_id)
            )

        invented = extract_numbers(bullet.text) - extract_numbers(source.bullet)
        if invented:
            problems.append(
                Problem(
                    "invented_number",
                    f"numbers not present in the source fact: {sorted(invented)}",
                    bullet.fact_id,
                )
            )

        if source.role != bullet.role_id:
            problems.append(
                Problem(
                    "misattributed_bullet",
                    f"placed under {bullet.role_id!r} but belongs to {source.role!r}",
                    bullet.fact_id,
                )
            )

    declared = profile.declared_skills
    for category, skills in selection.skills.items():
        for skill in skills:
            if skill not in declared:
                problems.append(
                    Problem("undeclared_skill", f"{skill!r} in category {category!r}")
                )

    supported = {
        skill
        for b in selection.bullets
        if b.fact_id in fact_by_id
        for skill in fact_by_id[b.fact_id].skills
    }
    for category, skills in selection.skills.items():
        for skill in skills:
            if skill not in supported:
                problems.append(
                    Problem(
                        "unsupported_skill",
                        f"{skill!r} is on the page but no selected bullet supports it",
                    )
                )

    return problems


def verify_pdf(pdf_path: str | Path, profile: Profile, selection: Selection) -> list[Problem]:
    """Read the rendered PDF back and confirm an ATS would see what we meant."""
    problems: list[Problem] = []
    text = normalise(extract_text(str(pdf_path)))

    if not text:
        return [Problem("no_text_layer", "the PDF has no extractable text at all")]

    identity = profile.identity
    for label, value in (
        ("name", identity.name),
        ("email", identity.email),
        ("phone", identity.phone),
        ("location", identity.location),
    ):
        if normalise(value) not in text:
            problems.append(Problem("missing_contact_detail", f"{label} ({value!r}) not found"))

    cursor = 0
    for bullet in selection.bullets:
        needle = normalise(bullet.text)
        found = text.find(needle, cursor)
        if found == -1:
            if needle in text:
                problems.append(
                    Problem("bullet_out_of_order", "appears earlier than expected", bullet.fact_id)
                )
            else:
                problems.append(
                    Problem("bullet_missing_from_pdf", "not found in extracted text", bullet.fact_id)
                )
        else:
            cursor = found + len(needle)

    return problems
