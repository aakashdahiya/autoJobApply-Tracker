"""Keep a Google Sheet in step with the tracker.

SQLite stays the source of truth. The sheet is a view you can read on a phone
and edit in two columns — Status and Notes — and nothing else travels back.
That asymmetry is the whole design: three writers against a spreadsheet with
no transactions and no unique constraints is how the first version of this
project would have corrupted itself.

The sync is pull-then-push:

1. **Pull.** Read the sheet. A cell counts as *your* edit only when it differs
   from what the last push wrote there. Without that memory, a sync either
   ignores your edits or re-applies stale ones over the database's own
   progress — an email-driven `rejected` would be dragged back to `applied`
   every night by a cell nobody had touched in weeks.
2. **Push.** Rewrite every row from the database, including the cells you just
   edited, so the sheet and the database agree again.

Columns are located by their header text, not by position, so rearranging or
inserting columns in the sheet does not corrupt the sync.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select

from api.db import Application, Company, Job, Status
from api.store import log, set_status

SHEET_NAME = "Applications"
ID_HEADER = "ID"
EDITABLE = ("Status", "Notes")

# (header, how to read it out of an application)
COLUMNS: tuple[tuple[str, str], ...] = (
    (ID_HEADER, "id"),
    ("Company", "company"),
    ("Title", "title"),
    ("Location", "location"),
    ("Status", "status"),
    ("Score", "score"),
    ("Shape", "shape"),
    ("Applied", "applied"),
    ("Source", "source"),
    ("Apply URL", "apply_url"),
    ("Resume", "resume"),
    ("Next action", "next_action"),
    ("Notes", "notes"),
    ("Updated", "updated"),
)
HEADERS = [header for header, _ in COLUMNS]


class SheetIO(Protocol):
    """The two operations this needs from a spreadsheet."""

    def read(self, a1: str) -> list[list[str]]: ...

    def write(self, a1: str, values: list[list[str]]) -> None: ...


@dataclass
class SyncReport:
    rows_written: int = 0
    status_changes: list[str] = field(default_factory=list)
    note_changes: int = 0
    rejected: list[str] = field(default_factory=list)
    unknown_rows: int = 0

    def summary(self) -> str:
        parts = [f"{self.rows_written} rows written"]
        if self.status_changes:
            parts.append(f"{len(self.status_changes)} status edits pulled in")
        if self.note_changes:
            parts.append(f"{self.note_changes} note edits pulled in")
        if self.rejected:
            parts.append(f"{len(self.rejected)} rejected")
        if self.unknown_rows:
            parts.append(f"{self.unknown_rows} unrecognised rows left alone")
        return ", ".join(parts)


def _cell(application: Application, key: str) -> str:
    job: Job = application.job
    if key == "id":
        return str(application.id)
    if key == "company":
        return job.company.name
    if key == "title":
        return job.title
    if key == "location":
        return ", ".join(job.locations or [])
    if key == "status":
        return application.status.value
    if key == "score":
        return f"{application.match_score:.0f}" if application.match_score is not None else ""
    if key == "shape":
        return application.shape or ""
    if key == "applied":
        return f"{application.applied_at:%Y-%m-%d}" if application.applied_at else ""
    if key == "source":
        return job.source
    if key == "apply_url":
        return job.apply_url
    if key == "resume":
        return application.resume_path or ""
    if key == "next_action":
        return application.next_action or ""
    if key == "notes":
        return application.notes or ""
    if key == "updated":
        return f"{application.last_status_change_at:%Y-%m-%d %H:%M}"
    return ""


def row_for(application: Application) -> list[str]:
    return [_cell(application, key) for _header, key in COLUMNS]


def applications(session) -> list[Application]:
    return list(
        session.scalars(
            select(Application).join(Job).join(Company).order_by(Application.id)
        )
    )


def header_index(existing: list[list[str]]) -> dict[str, int]:
    """Map header text to column position, so a reordered sheet still syncs."""
    if not existing:
        return {}
    return {name.strip(): i for i, name in enumerate(existing[0]) if name.strip()}


def pull(session, existing: list[list[str]]) -> SyncReport:
    """Apply the edits you made, and only those."""
    report = SyncReport()
    index = header_index(existing)
    if ID_HEADER not in index:
        return report

    by_id = {a.id: a for a in applications(session)}
    valid = {s.value for s in Status}

    for row in existing[1:]:
        def value(header: str) -> str:
            position = index.get(header)
            return row[position].strip() if position is not None and position < len(row) else ""

        raw_id = value(ID_HEADER)
        if not raw_id.isdigit():
            continue
        application = by_id.get(int(raw_id))
        if application is None:
            report.unknown_rows += 1
            continue

        in_sheet = value("Status")
        if in_sheet and in_sheet != (application.sheet_status_written or ""):
            if in_sheet not in valid:
                report.rejected.append(f"row for #{application.id}: unknown status {in_sheet!r}")
            elif in_sheet != application.status.value:
                previous = application.status.value
                set_status(session, application, Status(in_sheet), source="sheet")
                report.status_changes.append(
                    f"#{application.id} {previous} -> {in_sheet}"
                )

        notes = value("Notes")
        if notes != (application.sheet_notes_written or "") and notes != (application.notes or ""):
            application.notes = notes or None
            log(session, application, "notes_edited", "sheet", {"notes": notes})
            report.note_changes += 1

    session.commit()
    return report


def push(session, io: SheetIO, *, sheet: str = SHEET_NAME) -> int:
    """Rewrite the sheet from the database, and remember what we wrote."""
    rows = applications(session)
    values = [HEADERS] + [row_for(a) for a in rows]
    io.write(f"{sheet}!A1", values)

    status_at = HEADERS.index("Status")
    notes_at = HEADERS.index("Notes")
    for application, row in zip(rows, values[1:]):
        application.sheet_status_written = row[status_at]
        application.sheet_notes_written = row[notes_at]
    session.commit()
    return len(rows)


def sync(session, io: SheetIO, *, sheet: str = SHEET_NAME) -> SyncReport:
    """Pull your edits in, then push everything back out."""
    existing = io.read(f"{sheet}!A1:Z10000")
    report = pull(session, existing)
    report.rows_written = push(session, io, sheet=sheet)
    return report
