"""The checks that make the no-fabrication rule mechanical.

Two layers:

* `check_selection` runs before rendering. Every bullet must trace to a real
  fact, carry no number its source fact lacks, and sit under the entry it
  belongs to. In Phase 0 nothing rephrases, so `strict` also demands identical
  text; Phase 3 relaxes exactly that one check when constrained rephrasing
  arrives, and the rest keep working unchanged.

* `verify_pdf` runs after rendering: the text is pulled back out of the
  finished PDF and checked for every bullet, in order, with contact details
  present.

`reading_order_risks` is separate and advisory. Right-aligned dates are
positioned correctly in the PDF, but some extractors group them into the
following paragraph, so a date can read as if it were inside a bullet. That is
a layout risk worth knowing about, not a content error.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

from resume.markup import strip_markup
from resume.schema import Profile
from resume.select import Selection

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}

# Strict position ordering reflects the PDF's true content order. The default
# heuristic is what a naive parser does, which is what `reading_order_risks`
# checks against.
TRUE_ORDER = LAParams(boxes_flow=None)
NAIVE_ORDER = LAParams()


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

    Extraction reflows lines, resolves ligatures inconsistently and renders
    dashes in whatever form the font supplies; none of that may fail a
    comparison about content.
    """
    text = unicodedata.normalize("NFKC", strip_markup(text))
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    for dash in ("–", "—", "−"):
        text = text.replace(dash, "-")
    text = text.replace("→", "->").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
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

        invented = extract_numbers(bullet.plain) - extract_numbers(source.plain)
        if invented:
            problems.append(
                Problem(
                    "invented_number",
                    f"numbers not present in the source fact: {sorted(invented)}",
                    bullet.fact_id,
                )
            )

        if source.anchor != bullet.anchor_id:
            problems.append(
                Problem(
                    "misattributed_bullet",
                    f"placed under {bullet.anchor_id!r} but belongs to {source.anchor!r}",
                    bullet.fact_id,
                )
            )

    return problems


def _extracted(pdf_path: str | Path, laparams: LAParams) -> str:
    return normalise(extract_text(str(pdf_path), laparams=laparams))


def verify_pdf(pdf_path: str | Path, profile: Profile, selection: Selection) -> list[Problem]:
    """Read the rendered PDF back and confirm the text layer says what we meant."""
    problems: list[Problem] = []
    text = _extracted(pdf_path, TRUE_ORDER)

    if not text:
        return [Problem("no_text_layer", "the PDF has no extractable text at all")]

    identity = profile.identity
    for label, value in (
        ("name", identity.name),
        ("email", identity.email),
        ("phone", identity.phone),
        ("city", f"{identity.city}, {identity.province}"),
    ):
        if normalise(value) not in text:
            problems.append(Problem("missing_contact_detail", f"{label} ({value!r}) not found"))

    cursor = 0
    for bullet in selection.bullets:
        needle = normalise(bullet.text)
        found = text.find(needle, cursor)
        if found == -1:
            kind = "bullet_out_of_order" if needle in text else "bullet_missing_from_pdf"
            problems.append(Problem(kind, "text layer does not match", bullet.fact_id))
        else:
            cursor = found + len(needle)

    return problems


def reading_order_risks(pdf_path: str | Path, selection: Selection) -> list[Problem]:
    """Advisory: bullets a naive extractor would read as interrupted.

    A right-aligned date or tech line sits on the same visual line as the text
    to its left. The PDF orders it correctly, but a parser that groups by
    proximity can emit it in the middle of the following bullet, so the
    sentence arrives at the employer mangled. Layout risk, not a content error.
    """
    text = _extracted(pdf_path, NAIVE_ORDER)
    return [
        Problem("interrupted_bullet", "a naive parser reads this bullet as split", b.fact_id)
        for b in selection.bullets
        if normalise(b.text) not in text
    ]
