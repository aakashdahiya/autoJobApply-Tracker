"""The tiny bit of inline markup a resume bullet needs.

Bullets may bold a phrase with `**like this**` — the way the source resume
bolds metrics and technology names. Everything that reasons about *content*
(traceability, numbers, PDF extraction) works on the stripped text, so markup
can never smuggle a claim past a check.
"""

from __future__ import annotations

import re

BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)


def strip_markup(text: str) -> str:
    """The plain words, exactly as they will appear on the page."""
    return BOLD.sub(r"\1", text)


def parse_runs(text: str) -> list[dict]:
    """Split into [{text, bold}] runs for the renderers.

    Both the PDF and DOCX paths consume this, so bolding cannot drift between
    the two formats.
    """
    runs: list[dict] = []
    cursor = 0
    for match in BOLD.finditer(text):
        if match.start() > cursor:
            runs.append({"text": text[cursor : match.start()], "bold": False})
        runs.append({"text": match.group(1), "bold": True})
        cursor = match.end()
    if cursor < len(text):
        runs.append({"text": text[cursor:], "bold": False})
    return [r for r in runs if r["text"]]
