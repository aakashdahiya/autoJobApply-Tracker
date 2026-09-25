"""Render the same selection to DOCX, matching the source resume's formatting.

Calibri 10pt, 0.5in/0.6in margins, 17pt name, 11pt bold section headings with a
hairline bottom rule, a right tab stop at the text-area edge for dates, and no
tables anywhere.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from resume.schema import Profile
from resume.select import Selection
from resume.typst_render import build_payload

FONT = "Calibri"
RIGHT_TAB = Inches(7.3)  # 8.5in page less 0.6in margins


def _set_font(run, size: float, bold: bool = False) -> None:
    run.bold = bold
    run.font.name = FONT
    run.font.size = Pt(size)


def _para(doc, *, before: float = 0, after: float = 2, tab: bool = False):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    if tab:
        p.paragraph_format.tab_stops.add_tab_stop(RIGHT_TAB, WD_TAB_ALIGNMENT.RIGHT)
    return p


def _bottom_rule(p) -> None:
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:color"), "444444")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    pbdr.append(bottom)
    p._p.get_or_add_pPr().append(pbdr)


def _section(doc, title: str) -> None:
    p = _para(doc, before=7, after=3)
    _set_font(p.add_run(title.upper()), 11, bold=True)
    _bottom_rule(p)


def _heading_with_trailing(doc, parts: list[tuple[str, bool]], trailing: str | None,
                           date_style: str) -> None:
    """A heading line whose date or tech list sits at the right margin."""
    inline = date_style == "inline" or not trailing
    p = _para(doc, before=4, after=0, tab=not inline)
    for text, bold in parts:
        _set_font(p.add_run(text), 10, bold=bold)
    if trailing:
        _set_font(p.add_run(f" | {trailing}" if inline else f"\t{trailing}"), 10)


def _bullet(doc, runs: list[dict]) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(1)
    for run in runs:
        _set_font(p.add_run(run["text"]), 10, bold=run["bold"])


def render_docx(
    profile: Profile,
    selection: Selection,
    out_path: str | Path,
    *,
    date_style: str = "tab",
    work_auth_line: bool = False,
) -> Path:
    data = build_payload(
        profile, selection, date_style=date_style, work_auth_line=work_auth_line
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    for s in doc.sections:  # no header, no footer
        s.top_margin = s.bottom_margin = Inches(0.5)
        s.left_margin = s.right_margin = Inches(0.6)
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(2)

    _set_font(_para(doc, after=3).add_run(data["identity"]["name"].upper()), 17, bold=True)
    _set_font(_para(doc, after=1).add_run("  |  ".join(data["contact_primary"])), 10)
    if data["contact_links"]:
        _set_font(_para(doc, after=1).add_run("  |  ".join(data["contact_links"])), 10)
    if data["work_auth_line"]:
        _set_font(_para(doc, after=1).add_run(data["work_auth_line"]), 9.5)

    if data["summary"]:
        _section(doc, "Summary")
        _set_font(_para(doc).add_run(data["summary"]), 10)

    if data["experience"]:
        _section(doc, "Experience")
        for role in data["experience"]:
            _heading_with_trailing(
                doc,
                [(role["title"], True), (f" | {role['company']} ({role['location']})", False)],
                role["dates"],
                date_style,
            )
            if role["tech"]:
                p = _para(doc, before=1, after=1)
                _set_font(p.add_run("Tech: "), 10, bold=True)
                _set_font(p.add_run(", ".join(role["tech"])), 10)
            for runs in role["bullets"]:
                _bullet(doc, runs)

    if data["projects"]:
        _section(doc, "Projects")
        for proj in data["projects"]:
            name = proj["name"] + (f" — {proj['tagline']}" if proj["tagline"] else "")
            _heading_with_trailing(
                doc, [(name, True)], ", ".join(proj["tech"]) or None, date_style
            )
            for runs in proj["bullets"]:
                _bullet(doc, runs)
            if proj["link"]:
                _bullet(doc, [{"text": f"Source code and recorded demo: {proj['link']}",
                               "bold": False}])

    if data["skills"]:
        _section(doc, "Technical Skills")
        for group in data["skills"]:
            p = _para(doc, before=1, after=1)
            _set_font(p.add_run(f"{group['category']}: "), 10, bold=True)
            _set_font(p.add_run(", ".join(group["items"])), 10)

    if data["education"]:
        _section(doc, "Education")
        for e in data["education"]:
            _heading_with_trailing(
                doc,
                [(e["credential"], True), (f" | {e['institution']}, {e['location']}", False)],
                e["dates"],
                date_style,
            )
            if e["note"]:
                _set_font(_para(doc, before=1, after=1).add_run(e["note"]), 10)

    doc.save(str(out))
    return out
