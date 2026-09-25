"""The mirror: what travels back from the sheet, and what never does."""

from __future__ import annotations

import pytest

from api.db import Application, Status, make_engine, make_session_factory
from api.store import capture_job, set_status
from sheets import mirror
from sheets.mirror import HEADERS, ID_HEADER, pull, push, row_for, sync


class FakeSheet:
    """A spreadsheet as a grid, with the two operations the mirror uses."""

    def __init__(self, values=None):
        self.values = values or []
        self.writes = 0

    def read(self, a1):
        return [list(row) for row in self.values]

    def write(self, a1, values):
        self.values = [list(row) for row in values]
        self.writes += 1

    def column(self, header):
        return HEADERS.index(header)

    def cell(self, row_index, header):
        row = self.values[row_index]
        position = self.column(header)
        return row[position] if position < len(row) else ""

    def edit(self, row_index, header, value):
        row = self.values[row_index]
        while len(row) <= self.column(header):
            row.append("")
        row[self.column(header)] = value


@pytest.fixture
def session():
    s = make_session_factory(make_engine("sqlite://"))()
    yield s
    s.close()


def an_application(session, company="Cohere", title="Senior AI Engineer", status=None):
    _job, application, _ = capture_job(session, {
        "company": company, "title": title, "apply_url": f"https://x/{title}",
        "location": "Toronto, ON", "description": "Python, RAG",
    })
    if status:
        set_status(session, application, status)
    return application


# --- push -------------------------------------------------------------------

def test_push_writes_a_header_and_one_row_per_application(session):
    an_application(session, "Cohere")
    an_application(session, "Clio", "Backend Engineer")
    sheet = FakeSheet()

    assert push(session, sheet) == 2
    assert sheet.values[0] == HEADERS
    assert len(sheet.values) == 3
    assert {sheet.cell(1, "Company"), sheet.cell(2, "Company")} == {"Cohere", "Clio"}


def test_pushed_rows_carry_the_id_so_rows_are_matched_not_counted(session):
    application = an_application(session)
    sheet = FakeSheet()
    push(session, sheet)
    assert sheet.cell(1, ID_HEADER) == str(application.id)


def test_push_remembers_what_it_wrote_in_the_editable_cells(session):
    application = an_application(session, status=Status.applied)
    push(session, FakeSheet())
    assert application.sheet_status_written == "applied"


# --- pull: genuine edits ----------------------------------------------------

def test_a_status_you_type_into_the_sheet_is_applied(session):
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)

    sheet.edit(1, "Status", "interview")
    report = pull(session, sheet.read(""))

    assert session.get(Application, application.id).status is Status.interview
    assert report.status_changes and "interview" in report.status_changes[0]


def test_notes_you_type_are_kept(session):
    application = an_application(session)
    sheet = FakeSheet()
    push(session, sheet)

    sheet.edit(1, "Notes", "referred by Sam")
    pull(session, sheet.read(""))
    assert session.get(Application, application.id).notes == "referred by Sam"


# --- pull: what must NOT travel back ----------------------------------------

def test_an_untouched_cell_never_overwrites_the_database(session):
    """The bug this design exists to prevent.

    Email triage moves an application to `rejected`. The sheet still shows the
    `applied` we wrote last night. Without remembering our own write, the next
    sync reads that stale cell as an instruction and drags the row back.
    """
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)

    set_status(session, application, Status.rejected, source="gmail")
    pull(session, sheet.read(""))  # the sheet still says "applied"

    assert session.get(Application, application.id).status is Status.rejected


def test_editing_a_read_only_column_changes_nothing(session):
    application = an_application(session, "Cohere")
    sheet = FakeSheet()
    push(session, sheet)

    sheet.edit(1, "Company", "Definitely Not Cohere")
    sheet.edit(1, "Score", "999")
    pull(session, sheet.read(""))

    assert application.job.company.name == "Cohere"
    assert application.match_score is None


def test_a_read_only_edit_is_overwritten_on_the_next_push(session):
    an_application(session, "Cohere")
    sheet = FakeSheet()
    push(session, sheet)
    sheet.edit(1, "Company", "Typo Inc")

    sync(session, sheet)
    assert sheet.cell(1, "Company") == "Cohere"


def test_an_invalid_status_is_reported_not_applied(session):
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)

    sheet.edit(1, "Status", "definitely hired probably")
    report = pull(session, sheet.read(""))

    assert session.get(Application, application.id).status is Status.applied
    assert report.rejected and "unknown status" in report.rejected[0]


def test_a_row_with_an_unknown_id_is_left_alone(session):
    an_application(session)
    sheet = FakeSheet()
    push(session, sheet)
    sheet.values.append(["9999"] + [""] * (len(HEADERS) - 1))

    report = pull(session, sheet.read(""))
    assert report.unknown_rows == 1


def test_a_row_someone_typed_by_hand_is_ignored(session):
    an_application(session)
    sheet = FakeSheet()
    push(session, sheet)
    sheet.values.append(["", "Some Company", "A job I found"])

    assert pull(session, sheet.read("")).unknown_rows == 0  # no id, so not ours to touch


# --- robustness -------------------------------------------------------------

def test_columns_are_found_by_header_so_reordering_is_safe(session):
    """Someone will drag a column. It must not corrupt the sync."""
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)

    status_at, id_at = HEADERS.index("Status"), HEADERS.index(ID_HEADER)
    for row in sheet.values:
        row[status_at], row[id_at] = row[id_at], row[status_at]

    swapped = sheet.read("")
    swapped[1][id_at] = "interview"  # the Status cell, now in the old ID position
    pull(session, swapped)

    assert session.get(Application, application.id).status is Status.interview


def test_a_short_row_does_not_raise(session):
    an_application(session)
    sheet = FakeSheet()
    push(session, sheet)
    sheet.values[1] = sheet.values[1][:3]
    assert pull(session, sheet.read("")).status_changes == []


def test_an_empty_sheet_pulls_nothing_and_pushes_everything(session):
    an_application(session)
    sheet = FakeSheet()
    report = sync(session, sheet)
    assert report.rows_written == 1 and not report.status_changes


def test_sync_is_pull_then_push_so_your_edit_survives_it(session):
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)
    sheet.edit(1, "Status", "interview")

    sync(session, sheet)
    assert session.get(Application, application.id).status is Status.interview
    assert sheet.cell(1, "Status") == "interview"


def test_syncing_twice_changes_nothing_the_second_time(session):
    an_application(session, status=Status.applied)
    sheet = FakeSheet()
    sync(session, sheet)
    first = [list(row) for row in sheet.values]

    second = sync(session, sheet)
    assert sheet.values == first
    assert not second.status_changes and not second.rejected


def test_a_status_change_from_the_sheet_is_recorded_as_an_event(session):
    application = an_application(session, status=Status.applied)
    sheet = FakeSheet()
    push(session, sheet)
    sheet.edit(1, "Status", "interview")
    pull(session, sheet.read(""))

    kinds = [(e.kind, e.source) for e in session.get(Application, application.id).events]
    assert ("status_changed", "sheet") in kinds
