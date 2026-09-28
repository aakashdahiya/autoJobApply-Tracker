"""The push that must not wait for the 08:00 digest.

An online assessment with a 72-hour window is the single most expensive thing
to miss in this pipeline, and a digest that arrives tomorrow morning has
already spent a third of that window. So `interview_invite`, `assessment` and
`offer` go out the moment the sweep sees them; everything else waits.

Two things this module refuses to get wrong:

**A push is sent once.** The sweep is safe to re-run, and re-running it must
not re-ping you about an assessment you read yesterday. `EmailLink.notified_at`
records the send, and only null-stamped rows are candidates.

**A push that failed is still owed.** The stamp is written after the transport
returns, never before, so a send that dies on a flat network leaves the row
pending and the next sweep picks it up. The alternative — stamping first —
loses the notification permanently, because the message's own idempotency key
means it is never triaged again.

Transports are chosen by environment, never by a value in the repository:

    JOB_TRACKER_NOTIFY=telegram   TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...
    JOB_TRACKER_NOTIFY=email      NOTIFY_SMTP_HOST=...    NOTIFY_EMAIL_TO=...
    JOB_TRACKER_NOTIFY=console    (the default: prints, sends nothing)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select

from api.db import Application, EmailLink, utcnow
from inbox.digest import URGENT_KINDS

TIMEOUT = 15


class PushFailed(RuntimeError):
    """The transport could not deliver. The row stays pending for the retry."""


class Transport(Protocol):
    name: str

    def send(self, title: str, body: str) -> None:
        """Deliver one notification, or raise `PushFailed`."""


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

@dataclass
class ConsoleTransport:
    """Prints. The default, so the feature works before any account exists and
    the tests never need a network."""

    name: str = "console"
    sent: list[tuple[str, str]] = field(default_factory=list)

    def send(self, title: str, body: str) -> None:
        self.sent.append((title, body))
        print(f"\n*** {title}\n{body}\n")


@dataclass
class TelegramTransport:
    """A bot message. Least friction of the options: no SMTP server, no app
    password, and it reaches a phone lock screen."""

    token: str
    chat_id: str
    name: str = "telegram"

    def send(self, title: str, body: str) -> None:
        payload = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": f"{title}\n\n{body}",
            "disable_web_page_preview": "false",
        }).encode()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        request = urllib.request.Request(url, data=payload)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                answer = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            raise PushFailed(f"telegram unreachable: {error}") from error
        if not answer.get("ok"):
            # Telegram answers 200 with ok=false for a bad chat id or a revoked
            # token, which would otherwise look like a successful send.
            raise PushFailed(f"telegram refused: {answer.get('description', answer)}")


@dataclass
class EmailTransport:
    """Mail to yourself, for when a bot token is more setup than you want."""

    host: str
    to: str
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    name: str = "email"

    def send(self, title: str, body: str) -> None:
        import smtplib
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = title
        message["From"] = self.sender or self.username or self.to
        message["To"] = self.to
        message.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=TIMEOUT) as smtp:
                smtp.ehlo()
                if self.port != 25:
                    smtp.starttls()
                    smtp.ehlo()
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise PushFailed(f"smtp failed: {error}") from error


def from_env(env: dict[str, str] | None = None) -> Transport:
    """Build the configured transport. Falls back to the console, which is the
    honest default: it says what would have been sent rather than pretending."""
    env = os.environ if env is None else env
    choice = (env.get("JOB_TRACKER_NOTIFY") or "console").strip().casefold()

    if choice == "telegram":
        token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
        if not (token and chat):
            raise PushFailed(
                "JOB_TRACKER_NOTIFY=telegram needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
        return TelegramTransport(token=token, chat_id=chat)

    if choice == "email":
        host, to = env.get("NOTIFY_SMTP_HOST"), env.get("NOTIFY_EMAIL_TO")
        if not (host and to):
            raise PushFailed(
                "JOB_TRACKER_NOTIFY=email needs NOTIFY_SMTP_HOST and NOTIFY_EMAIL_TO"
            )
        return EmailTransport(
            host=host,
            to=to,
            port=int(env.get("NOTIFY_SMTP_PORT") or 587),
            username=env.get("NOTIFY_SMTP_USER") or "",
            password=env.get("NOTIFY_SMTP_PASSWORD") or "",
            sender=env.get("NOTIFY_EMAIL_FROM") or "",
        )

    if choice in {"console", "none", ""}:
        return ConsoleTransport()

    raise PushFailed(f"unknown JOB_TRACKER_NOTIFY value: {choice!r}")


# ---------------------------------------------------------------------------
# What to send, and to whom it belongs
# ---------------------------------------------------------------------------

@dataclass
class PushReport:
    sent: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def pending(session) -> list[EmailLink]:
    """Urgent messages that have not been pushed yet, soonest deadline first."""
    return list(session.scalars(
        select(EmailLink)
        .where(
            EmailLink.classification.in_(URGENT_KINDS),
            EmailLink.notified_at.is_(None),
        )
        .order_by(EmailLink.deadline_at.is_(None), EmailLink.deadline_at)
    ))


def compose(session, link: EmailLink, *, now: dt.datetime | None = None) -> tuple[str, str]:
    """Title and body for one push. Leads with the deadline, because that is
    the part that decides whether you stop what you are doing."""
    now = now or utcnow()
    kind = link.classification.replace("_", " ")

    company = ""
    if link.application_id is not None:
        application = session.get(Application, link.application_id)
        if application is not None:
            company = application.job.company.name

    title = f"{kind.title()} — {company}" if company else kind.title()

    lines = [link.subject or "(no subject)"]
    deadline = _aware(link.deadline_at)
    if deadline:
        hours = (deadline - now).total_seconds() / 3600
        lines.append(
            f"DUE {deadline:%a %d %b %H:%M} — {hours:.0f}h left" if hours >= 0
            else f"DEADLINE PASSED {deadline:%a %d %b %H:%M}"
        )
    if link.sender:
        lines.append(f"from {link.sender}")
    for url in (link.extracted or {}).get("scheduling_links") or []:
        lines.append(url)
    if link.application_id is None:
        lines.append("(not matched to an application — place it in the digest)")
    return title, "\n".join(lines)


def push_pending(session, transport: Transport | None = None, *,
                 now: dt.datetime | None = None) -> PushReport:
    """Send every owed push, stamping each row only once it is delivered."""
    transport = transport or from_env()
    report = PushReport()

    for link in pending(session):
        title, body = compose(session, link, now=now)
        try:
            transport.send(title, body)
        except PushFailed as error:
            # Left unstamped on purpose: the next sweep owes this one again.
            report.failed += 1
            report.errors.append(f"{link.subject or link.gmail_message_id}: {error}")
            continue
        link.notified_at = utcnow()
        report.sent += 1

    session.commit()
    return report
