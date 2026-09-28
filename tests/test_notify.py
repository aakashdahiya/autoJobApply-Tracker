"""Instant pushes: sent once, and still owed when the transport fails.

The whole point of this path is an assessment with a 72-hour window. Both
halves matter — a second ping about yesterday's email trains you to ignore
them, and a silently dropped one costs you the deadline.
"""

from __future__ import annotations

import datetime as dt

import pytest

from api.db import EmailLink, Status, make_engine, make_session_factory
from api.store import capture_job
from inbox.notify import (
    ConsoleTransport,
    EmailTransport,
    PushFailed,
    PushReport,
    TelegramTransport,
    compose,
    from_env,
    pending,
    push_pending,
)


@pytest.fixture
def session():
    s = make_session_factory(make_engine("sqlite://"))()
    yield s
    s.close()


class DeadTransport:
    """Every send fails, the way a flat network does."""

    name = "dead"

    def __init__(self):
        self.attempts = 0

    def send(self, title, body):
        self.attempts += 1
        raise PushFailed("no route to host")


def add_link(session, *, kind="assessment", subject="Online assessment",
             deadline=None, application_id=None, sender="ta@cohere.com",
             message_id=None, extracted=None):
    link = EmailLink(
        gmail_message_id=message_id or f"msg-{kind}-{subject}-{session.query(EmailLink).count()}",
        classification=kind,
        confidence=0.9,
        sender=sender,
        subject=subject,
        deadline_at=deadline,
        application_id=application_id,
        extracted=extracted or {},
    )
    session.add(link)
    session.commit()
    return link


def add_application(session, company="Cohere", title="Senior AI Engineer"):
    """Through the real capture path, so the row looks like a captured job."""
    _job, application, _created = capture_job(session, {
        "title": title, "company": company,
        "apply_url": f"https://jobs.example.com/{title}",
    })
    application.status = Status.applied
    session.commit()
    return application


# --- what gets picked up ----------------------------------------------------

def test_only_the_time_critical_kinds_are_pushed(session):
    for kind in ("assessment", "interview_invite", "offer"):
        add_link(session, kind=kind, subject=f"{kind} mail")
    for kind in ("rejection", "acknowledgement", "recruiter_outreach", "other"):
        add_link(session, kind=kind, subject=f"{kind} mail")

    kinds = {link.classification for link in pending(session)}
    assert kinds == {"assessment", "interview_invite", "offer"}


def test_the_soonest_deadline_is_pushed_first(session):
    now = dt.datetime(2026, 9, 28, 9, 0)
    add_link(session, subject="later", deadline=now + dt.timedelta(days=3))
    add_link(session, subject="sooner", deadline=now + dt.timedelta(hours=6))
    add_link(session, subject="no deadline", deadline=None)

    assert [link.subject for link in pending(session)] == ["sooner", "later", "no deadline"]


# --- sent exactly once ------------------------------------------------------

def test_a_push_is_sent_once_however_often_the_sweep_runs(session):
    add_link(session, subject="Take-home, 72 hours")
    transport = ConsoleTransport()

    first = push_pending(session, transport)
    second = push_pending(session, transport)
    third = push_pending(session, transport)

    assert first.sent == 1
    assert (second.sent, third.sent) == (0, 0)
    assert len(transport.sent) == 1, "a re-run pinged about the same email again"


def test_delivery_is_stamped_on_the_row(session):
    link = add_link(session)
    assert link.notified_at is None
    push_pending(session, ConsoleTransport())
    session.refresh(link)
    assert link.notified_at is not None


# --- a failed push is still owed --------------------------------------------

def test_a_failed_push_is_retried_rather_than_lost(session):
    """The failure that matters: stamping before sending would drop this
    silently, and the message's own idempotency key means triage never sees
    it again."""
    add_link(session, subject="Take-home, 72 hours")
    dead = DeadTransport()

    failed = push_pending(session, dead)
    assert (failed.sent, failed.failed) == (0, 1)
    assert failed.errors and "no route to host" in failed.errors[0]

    # The network comes back.
    recovered = ConsoleTransport()
    retried = push_pending(session, recovered)
    assert retried.sent == 1
    assert len(recovered.sent) == 1


def test_one_dead_message_does_not_block_the_others(session):
    add_link(session, subject="first")
    add_link(session, subject="second")

    class FlakyTransport:
        name = "flaky"

        def __init__(self):
            self.sent = []

        def send(self, title, body):
            if "first" in body:
                raise PushFailed("refused")
            self.sent.append((title, body))

    report = push_pending(session, FlakyTransport())
    assert (report.sent, report.failed) == (1, 1)
    # and the one that failed is still queued
    assert [link.subject for link in pending(session)] == ["first"]


# --- what the message says --------------------------------------------------

def test_the_body_leads_with_the_deadline(session):
    now = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.timezone.utc)
    application = add_application(session)
    add_link(session, subject="Complete your HackerRank",
             deadline=dt.datetime(2026, 9, 30, 9, 0),
             application_id=application.id)

    title, body = compose(session, pending(session)[0], now=now)
    assert "Assessment" in title and "Cohere" in title
    assert "Complete your HackerRank" in body
    assert "DUE" in body and "48h left" in body


def test_a_passed_deadline_says_so_rather_than_showing_negative_hours(session):
    now = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.timezone.utc)
    add_link(session, subject="Assessment", deadline=dt.datetime(2026, 9, 26, 9, 0))

    _title, body = compose(session, pending(session)[0], now=now)
    assert "DEADLINE PASSED" in body
    assert "-48" not in body


def test_an_unmatched_email_still_pushes_and_says_it_is_unplaced(session):
    """An orphaned interview invite is still an interview invite."""
    add_link(session, kind="interview_invite", subject="Chat next week", application_id=None)

    title, body = compose(session, pending(session)[0])
    assert "Interview Invite" in title
    assert "not matched" in body


def test_scheduling_links_travel_with_the_push(session):
    add_link(session, kind="interview_invite", subject="Pick a slot",
             extracted={"scheduling_links": ["https://calendly.com/x/30min"]})

    _title, body = compose(session, pending(session)[0])
    assert "https://calendly.com/x/30min" in body


# --- transport selection ----------------------------------------------------

def test_console_is_the_default_so_nothing_is_needed_to_start():
    assert from_env({}).name == "console"


def test_telegram_is_built_from_the_environment():
    transport = from_env({
        "JOB_TRACKER_NOTIFY": "telegram",
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_CHAT_ID": "456",
    })
    assert isinstance(transport, TelegramTransport)
    assert transport.chat_id == "456"


def test_a_half_configured_transport_says_what_is_missing():
    with pytest.raises(PushFailed, match="TELEGRAM_BOT_TOKEN"):
        from_env({"JOB_TRACKER_NOTIFY": "telegram"})
    with pytest.raises(PushFailed, match="NOTIFY_SMTP_HOST"):
        from_env({"JOB_TRACKER_NOTIFY": "email"})


def test_email_transport_is_built_from_the_environment():
    transport = from_env({
        "JOB_TRACKER_NOTIFY": "email",
        "NOTIFY_SMTP_HOST": "smtp.gmail.com",
        "NOTIFY_EMAIL_TO": "me@example.com",
        "NOTIFY_SMTP_PORT": "465",
    })
    assert isinstance(transport, EmailTransport)
    assert transport.port == 465


def test_an_unknown_transport_is_refused_rather_than_silently_dropping_pushes():
    with pytest.raises(PushFailed, match="unknown"):
        from_env({"JOB_TRACKER_NOTIFY": "carrier-pigeon"})


def test_telegram_treats_ok_false_as_a_failure(monkeypatch):
    """Telegram answers HTTP 200 with ok=false for a revoked token. Reading
    only the status code would mark an undelivered push as sent."""
    import io
    import urllib.request

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *a, **k: Answer(b'{"ok": false, "description": "chat not found"}'),
    )
    with pytest.raises(PushFailed, match="chat not found"):
        TelegramTransport(token="t", chat_id="c").send("title", "body")


# --- the CLI contract -------------------------------------------------------

def _cli_env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("TRACKER_DB_URL", f"sqlite:///{tmp_path}/cli.sqlite")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


def _seed_urgent(tmp_path):
    from api.db import make_engine, make_session_factory
    session = make_session_factory(make_engine(f"sqlite:///{tmp_path}/cli.sqlite"))()
    add_link(session, subject="Take-home due Friday")
    session.close()


def test_cli_exits_non_zero_when_a_push_is_owed_but_undeliverable(monkeypatch, tmp_path, capsys):
    """Cron has to notice a broken transport. Exiting 0 while you hear nothing
    is the same silence the feature exists to prevent."""
    from inbox.run import main

    _cli_env(monkeypatch, tmp_path, JOB_TRACKER_NOTIFY="telegram")
    _seed_urgent(tmp_path)

    assert main(["--notify"]) == 1
    assert "undelivered" in capsys.readouterr().err


def test_cli_exits_zero_once_the_push_is_delivered(monkeypatch, tmp_path):
    from inbox.run import main

    _cli_env(monkeypatch, tmp_path, JOB_TRACKER_NOTIFY="console")
    _seed_urgent(tmp_path)

    assert main(["--notify"]) == 0
    assert main(["--notify"]) == 0  # nothing owed the second time


def test_cli_exits_zero_when_nothing_is_owed_even_if_unconfigured(monkeypatch, tmp_path):
    """A broken transport with no pending push has cost you nothing yet."""
    from inbox.run import main

    _cli_env(monkeypatch, tmp_path, JOB_TRACKER_NOTIFY="telegram")
    assert main(["--notify"]) == 0
