"""Render a selection to an ATS-safe PDF via Typst.

Data reaches the template as a JSON string in `sys.inputs`, so nothing is
interpolated into Typst source and there are no escaping bugs.
"""

from __future__ import annotations

import json
from pathlib import Path

import typst

from resume.markup import parse_runs
from resume.schema import Profile
from resume.select import Entry, Selection

TEMPLATE = Path(__file__).parent / "templates" / "resume.typ"


def _entry_bullets(entry: Entry) -> list[list[dict]]:
    return [parse_runs(b.text) for b in entry.bullets]


def build_payload(
    profile: Profile,
    selection: Selection,
    *,
    date_style: str = "tab",
    work_auth_line: bool = True,
) -> dict:
    """Flatten profile + selection into what the template expects."""
    identity = profile.identity

    return {
        "doc_title": f"{identity.name} — {selection.shape.value}",
        "date_style": date_style,
        "identity": {"name": identity.name, "email": identity.email, "phone": identity.phone},
        "contact_primary": [
            selection.headline,
            identity.location,
            identity.phone,
            identity.email,
        ],
        "contact_links": list(identity.links),
        "work_auth_line": profile.constraints.work_auth_line() if work_auth_line else None,
        "summary": selection.summary,
        "experience": [
            {
                "title": e.obj.title,
                "company": e.obj.company,
                "location": e.obj.location,
                "dates": e.obj.date_range(),
                "tech": list(e.obj.tech),
                "bullets": _entry_bullets(e),
            }
            for e in selection.experience
        ],
        "projects": [
            {
                "name": e.obj.name,
                "tagline": e.obj.tagline,
                "tech": list(e.obj.tech),
                "link": e.obj.link,
                "bullets": _entry_bullets(e),
            }
            for e in selection.projects
        ],
        "skills": [
            {"category": category, "items": items}
            for category, items in selection.skills.items()
        ],
        "education": [
            {
                "credential": e.credential,
                "institution": e.institution,
                "location": e.location,
                "dates": e.date_range(),
                "note": e.note,
            }
            for e in profile.education
        ],
    }


def render_pdf(
    profile: Profile,
    selection: Selection,
    out_path: str | Path,
    *,
    date_style: str = "tab",
    work_auth_line: bool = True,
) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(
        profile, selection, date_style=date_style, work_auth_line=work_auth_line
    )
    typst.compile(
        str(TEMPLATE),
        output=str(out),
        root=str(TEMPLATE.parent),
        sys_inputs={"data": json.dumps(payload, ensure_ascii=False)},
    )
    return out
