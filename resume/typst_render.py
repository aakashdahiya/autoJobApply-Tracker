"""Render a selection to an ATS-safe PDF via Typst.

Data reaches the template as a JSON string in `sys.inputs`, so there is no
string interpolation into Typst source and therefore no escaping bugs.
"""

from __future__ import annotations

import json
from pathlib import Path

import typst

from resume.schema import Profile
from resume.select import Selection

TEMPLATE = Path(__file__).parent / "templates" / "resume.typ"


def build_payload(profile: Profile, selection: Selection) -> dict:
    """Flatten profile + selection into what the template expects."""
    identity = profile.identity
    contact = [identity.location, identity.email, identity.phone]
    contact.extend(identity.links.values())

    return {
        "doc_title": f"{identity.name} — {selection.shape.value}",
        "identity": {
            "name": identity.name,
            "email": identity.email,
            "phone": identity.phone,
            "location": identity.location,
        },
        "contact": contact,
        "work_auth_line": profile.constraints.work_auth_line(),
        "summary": selection.summary,
        "skills": [
            {"category": category, "items": [profile.label(s) for s in items]}
            for category, items in selection.skills.items()
        ],
        "experience": [
            {
                "title": role.title,
                "company": role.company,
                "location": role.location,
                "dates": role.date_range(),
                "bullets": [b.text for b in bullets],
            }
            for role, bullets in selection.roles
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


def render_pdf(profile: Profile, selection: Selection, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_payload(profile, selection), ensure_ascii=False)
    typst.compile(
        str(TEMPLATE),
        output=str(out),
        root=str(TEMPLATE.parent),
        sys_inputs={"data": payload},
    )
    return out
