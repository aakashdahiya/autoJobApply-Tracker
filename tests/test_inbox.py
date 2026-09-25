"""Email triage: classification, matching, status movement, and the digest."""

from __future__ import annotations

import datetime as dt

import pytest

from api.db import (
    Application, EmailLink, Status, make_engine, make_session_factory, utcnow,
)
from api.store import capture_job, set_status
from inbox import digest as digest_module
from inbox.classify import find_deadline, triage
from inbox.match import match_email, sender_domain
from inbox.sync import Message, apply_message, ghost_stale, plain_text, sync, to_message

NOW = dt.datetime(2026, 9, 25, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    session = make_session_factory(engine)()
    yield session
    session.close()


def a_job(session, company="Cohere", title="Senior AI Engineer", domain=None):
    job, application, _ = capture_job(session, {
        "company": company, "title": title,
        "apply_url": f"https://jobs.example/{title}".replace(" ", "-"),
        "location": "Toronto, ON", "description": "Python, RAG, LLMs",
    })
    if domain:
        job.company.domain = domain
        session.commit()
    return job, application


# --- classification ---------------------------------------------------------

def test_a_rejection_that_opens_with_thanks_is_still_a_rejection():
    """Almost every rejection starts "thank you for applying"."""
    result = triage(
        "Your application to Cohere",
        "Thank you for applying. After careful review we have decided to move forward "
        "with other candidates.",
    )
    assert result.kind == "rejection"


def test_the_urgent_kinds_are_flagged():
    for subject, body, kind in [
        ("Next step", "Please complete the online assessment within 72 hours.", "assessment"),
        ("Chat?", "We would like to speak with you — please book a time.", "interview_invite"),
        ("Good news", "We are pleased to offer you the position.", "offer"),
    ]:
        result = triage(subject, body)
        assert result.kind == kind and result.urgent


def test_ordinary_mail_is_left_alone():
    assert triage("Weekly Python digest", "Top articles this week").kind == "other"
    assert triage("", "").kind == "other"


def test_a_recruiter_pitch_is_not_an_application_update():
    result = triage("Role at Shopify", "I came across your profile and wanted to reach out.")
    assert result.kind == "recruiter_outreach" and not result.urgent


def test_quoted_history_does_not_vote_twice():
    """Only the opening of the body is read, so an old rejection quoted under a
    new interview invite cannot outvote it."""
    body = (
        "We would like to speak with you. Book a time.\n\n"
        "On 1 Sep 2026 at 09:00, Cohere Talent wrote:\n"
        + "> we have decided to move forward with other candidates.\n" * 40
    )
    assert triage("Interview", body).kind == "interview_invite"


@pytest.mark.parametrize(
    "text,hours",
    [("complete this within 48 hours", 48), ("you have 72 hours", 72), ("within 3 days", 72)],
)
def test_relative_deadlines_are_resolved_against_the_received_time(text, hours):
    when, _ = find_deadline(text, received_at=NOW)
    assert when == NOW + dt.timedelta(hours=hours)


def test_absolute_deadlines_are_parsed():
    when, raw = find_deadline("please submit by 2026-10-02", received_at=NOW)
    assert when and when.date() == dt.date(2026, 10, 2) and "2026-10-02" in raw

    when, _ = find_deadline("due Friday", received_at=NOW)
    assert when and when.weekday() == 4


def test_a_scheduling_link_is_pulled_out():
    result = triage("Interview", "Book a time: https://calendly.com/rbc/30min thanks")
    assert result.scheduling_links == ["calendly.com"] or result.scheduling_links


# --- matching ---------------------------------------------------------------

def test_matching_by_sender_domain(session):
    _, application = a_job(session, "Cohere")
    found = match_email(session, sender="Talent <careers@cohere.com>",
                        subject="Update", body="")
    assert found.application_id == application.id


def test_matching_by_company_name_in_the_subject(session):
    _, application = a_job(session, "Cohere")
    found = match_email(session, sender="no-reply@us.greenhouse-mail.io",
                        subject="Your application to Cohere", body="")
    assert found.application_id == application.id


def test_an_ats_domain_alone_is_not_a_match(session):
    """Greenhouse sends for thousands of employers; the domain says nothing."""
    a_job(session, "Cohere")
    found = match_email(session, sender="no-reply@us.greenhouse-mail.io",
                        subject="An update on your application", body="")
    assert found.application_id is None


def test_two_applications_at_one_company_are_left_ambiguous(session):
    """Closing the wrong one hides a live application, so do not pick."""
    a_job(session, "Cohere", "Senior AI Engineer")
    a_job(session, "Cohere", "Backend Engineer")
    found = match_email(session, sender="careers@cohere.com", subject="Update", body="")
    assert found.application_id is None and found.ambiguous


def test_the_title_separates_two_applications_at_one_company(session):
    a_job(session, "Cohere", "Senior AI Engineer")
    _, backend = a_job(session, "Cohere", "Backend Engineer")
    found = match_email(session, sender="careers@cohere.com",
                        subject="Backend Engineer — next steps", body="")
    assert found.application_id == backend.id


def test_a_later_email_in_a_known_thread_inherits_the_match(session):
    _, application = a_job(session, "Cohere")
    session.add(EmailLink(gmail_message_id="m1", gmail_thread_id="t1",
                          application_id=application.id, classification="acknowledgement"))
    session.commit()
    found = match_email(session, sender="someone@random.example",
                        subject="Re: (no clues here)", body="", thread_id="t1")
    assert found.application_id == application.id


def test_unknown_mail_is_an_orphan_not_a_guess(session):
    a_job(session, "Cohere")
    assert match_email(session, sender="newsletter@python.org",
                       subject="Weekly digest", body="").application_id is None


# --- applying ---------------------------------------------------------------

def message(**kwargs):
    base = dict(id="m1", thread_id="t1", sender="careers@cohere.com",
                subject="Update", body="", received_at=NOW)
    base.update(kwargs)
    return Message(**base)


def test_a_rejection_email_closes_the_application(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    apply_message(session, message(body="We have decided to move forward with other candidates."))
    assert session.get(Application, application.id).status is Status.rejected


def test_an_interview_invite_advances_the_application(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    apply_message(session, message(body="We would like to speak with you. Book a time."))
    assert session.get(Application, application.id).status is Status.interview


def test_an_application_is_never_moved_backwards(session):
    """A late "we received your application" must not undo an interview."""
    _, application = a_job(session)
    set_status(session, application, Status.interview)
    apply_message(session, message(id="m2", body="We have received your application."))
    assert session.get(Application, application.id).status is Status.interview


def test_a_closed_application_is_not_reopened(session):
    _, application = a_job(session)
    set_status(session, application, Status.rejected)
    apply_message(session, message(id="m3", body="We have received your application."))
    assert session.get(Application, application.id).status is Status.rejected


def test_the_same_message_twice_changes_nothing(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    apply_message(session, message(body="We have received your application."))
    _, outcome = apply_message(session, message(body="We have received your application."))
    assert outcome == "duplicate"
    assert session.query(EmailLink).count() == 1


def test_an_orphan_is_recorded_with_its_reason(session):
    a_job(session)
    link, outcome = apply_message(session, message(
        id="m9", thread_id="t9", sender="news@python.org", subject="Weekly digest"))
    assert outcome == "orphan" and link.application_id is None
    assert link.extracted["match_reason"]


def test_an_assessment_deadline_is_stored(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    link, _ = apply_message(session, message(
        subject="Coding challenge",
        body="Please complete the online assessment within 72 hours."))
    assert link.deadline_at is not None
    assert session.get(Application, application.id).status is Status.assessment


# --- sync -------------------------------------------------------------------

class FakeSource:
    def __init__(self, messages, cursor="42"):
        self.messages, self.cursor, self.seen_cursor = messages, cursor, "unset"

    def fetch(self, cursor):
        self.seen_cursor = cursor
        return self.messages, self.cursor


def test_sync_reports_what_it_did_and_stores_the_cursor(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    source = FakeSource([
        message(id="a", body="We have decided to move forward with other candidates."),
        message(id="b", thread_id="t-other", sender="news@python.org",
                subject="Weekly digest"),
    ])
    report = sync(session, source)

    assert report.seen == 2 and report.linked == 1 and report.orphans == 1
    assert report.cursor == "42"
    assert source.seen_cursor is None, "first sweep has no cursor"

    sync(session, FakeSource([], cursor="43"))
    assert FakeSource([]).fetch("42")[1] == "42"


def test_the_second_sweep_resumes_from_the_stored_cursor(session):
    sync(session, FakeSource([], cursor="100"))
    second = FakeSource([], cursor="101")
    sync(session, second)
    assert second.seen_cursor == "100"


def test_urgent_items_are_surfaced_by_the_sweep(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    report = sync(session, FakeSource([message(
        subject="Coding challenge",
        body="Complete the online assessment within 24 hours.")]))
    assert report.urgent and "assessment" in report.urgent[0]


# --- ghosting and digest ----------------------------------------------------

def test_silence_becomes_ghosted_after_the_window(session):
    _, fresh = a_job(session, "Cohere", "AI Engineer")
    _, old = a_job(session, "BigCo", "Backend Engineer")
    set_status(session, fresh, Status.applied)
    set_status(session, old, Status.applied)
    old.last_status_change_at = utcnow().replace(tzinfo=None) - dt.timedelta(days=40)
    session.commit()

    ghosted = ghost_stale(session, days=21)
    assert ghosted == [old.id]
    assert session.get(Application, fresh.id).status is Status.applied


def test_digest_leads_with_what_is_time_critical(session):
    _, application = a_job(session)
    set_status(session, application, Status.applied)
    apply_message(session, message(
        subject="Coding challenge",
        body="Complete the online assessment within 24 hours."))

    built = digest_module.build(session, now=NOW)
    assert built.urgent and "assessment" in built.urgent[0].text
    text = digest_module.render(built)
    assert "NEEDS YOU NOW" in text and "Coding challenge" in text


def test_digest_lists_orphans_for_you_to_place(session):
    a_job(session)
    apply_message(session, message(id="z", sender="news@python.org", subject="Weekly digest"))
    text = digest_module.render(digest_module.build(session, now=NOW))
    assert "COULD NOT PLACE" in text and "Weekly digest" in text


def test_an_empty_digest_says_so(session):
    text = digest_module.render(digest_module.build(session, now=NOW))
    assert "Nothing needs you today." in text


# --- gmail payload parsing --------------------------------------------------

def test_plain_text_is_pulled_from_a_nested_multipart_payload():
    import base64

    encode = lambda s: base64.urlsafe_b64encode(s.encode()).decode()
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": encode("the real body")}},
                {"mimeType": "text/html", "body": {"data": encode("<p>html</p>")}},
            ]},
        ],
    }
    assert plain_text(payload) == "the real body"


def test_a_message_without_a_text_part_falls_back_to_the_snippet():
    raw = {"id": "x", "threadId": "t", "snippet": "just the preview",
           "payload": {"headers": [{"name": "From", "value": "a@b.com"}]}}
    assert to_message(raw).body == "just the preview"


def test_headers_are_read_case_insensitively():
    raw = {"id": "x", "threadId": "t", "snippet": "",
           "payload": {"headers": [
               {"name": "FROM", "value": "Talent <a@cohere.com>"},
               {"name": "subject", "value": "Hello"},
               {"name": "Date", "value": "Fri, 25 Sep 2026 09:00:00 +0000"},
           ]}}
    parsed = to_message(raw)
    assert parsed.sender.endswith("<a@cohere.com>")
    assert parsed.subject == "Hello"
    assert parsed.received_at and parsed.received_at.year == 2026


def test_a_malformed_date_header_does_not_break_the_message():
    raw = {"id": "x", "threadId": "t", "snippet": "hi",
           "payload": {"headers": [{"name": "Date", "value": "not a date"}]}}
    assert to_message(raw).received_at is None


def test_sender_domain_handles_subdomains_and_rubbish():
    assert sender_domain("a@jobs.rbc.ca") == "rbc.ca"
    assert sender_domain("nonsense") == ""
