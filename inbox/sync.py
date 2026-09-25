"""Pull new mail, triage it, and move applications accordingly.

The Gmail-specific code is deliberately thin and sits behind `MessageSource`:
everything that decides anything is in `classify`, `match` and `apply_message`,
all of which are tested against plain dictionaries. Swapping mail providers, or
fixing a Gmail field name, touches one class.

Incremental by `historyId` — a sweep reads the handful of messages that
arrived, never the whole mailbox.
"""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Iterable, Protocol

from sqlalchemy import select

from api.db import Application, EmailLink, Status, SyncState, utcnow
from api.store import log, set_status
from inbox.classify import triage
from inbox.match import match_email

# Which status a classification implies.
IMPLIES: dict[str, Status] = {
    "acknowledgement": Status.acknowledged,
    "assessment": Status.assessment,
    "interview_invite": Status.interview,
    "offer": Status.offer,
    "rejection": Status.rejected,
}

# How far along each status is. An email only ever moves an application
# forward, so a stray "we received your application" cannot undo an interview.
RANK = {
    Status.discovered: 0, Status.scored: 1, Status.tailored: 2, Status.ready: 3,
    Status.applied: 4, Status.acknowledged: 5, Status.screening: 6,
    Status.assessment: 7, Status.interview: 8, Status.offer: 9,
}
TERMINAL = {Status.rejected, Status.offer}

GHOST_AFTER_DAYS = 21


@dataclass
class Message:
    id: str
    thread_id: str = ""
    sender: str = ""
    subject: str = ""
    body: str = ""
    received_at: dt.datetime | None = None


class MessageSource(Protocol):
    def fetch(self, cursor: str | None) -> tuple[list[Message], str | None]:
        """Return (new messages, new cursor)."""


@dataclass
class SyncReport:
    seen: int = 0
    linked: int = 0
    orphans: int = 0
    duplicates: int = 0
    moved: list[str] = field(default_factory=list)
    urgent: list[str] = field(default_factory=list)
    cursor: str | None = None


def apply_message(session, message: Message) -> tuple[EmailLink | None, str]:
    """Triage one message and record it. Idempotent on the Gmail message id."""
    existing = session.scalar(
        select(EmailLink).where(EmailLink.gmail_message_id == message.id)
    )
    if existing is not None:
        return existing, "duplicate"

    verdict = triage(message.subject, message.body, received_at=message.received_at)
    found = match_email(
        session,
        sender=message.sender,
        subject=message.subject,
        body=message.body,
        thread_id=message.thread_id,
    )

    link = EmailLink(
        gmail_message_id=message.id,
        gmail_thread_id=message.thread_id or None,
        application_id=found.application_id,
        classification=verdict.kind,
        confidence=verdict.confidence,
        sender=message.sender[:300],
        subject=(message.subject or "")[:500],
        received_at=message.received_at,
        deadline_at=verdict.deadline_at,
        extracted={
            "scores": verdict.scores,
            "deadline_text": verdict.deadline_text,
            "scheduling_links": verdict.scheduling_links,
            "match_reason": found.reason,
            "match_score": found.score,
            "ambiguous": found.ambiguous,
        },
    )
    session.add(link)
    session.flush()

    if found.application_id is None:
        return link, "orphan"

    application = session.get(Application, found.application_id)
    log(session, application, "email_matched", "gmail", {
        "classification": verdict.kind,
        "subject": link.subject,
        "reason": found.reason,
    })

    target = IMPLIES.get(verdict.kind)
    if target is not None and _should_move(application.status, target):
        set_status(session, application, target, source="gmail", note=link.subject)
        return link, "moved"

    session.commit()
    return link, "linked"


def _should_move(current: Status, target: Status) -> bool:
    if current in TERMINAL:
        return False  # a closed application is not reopened by a stray email
    if target in TERMINAL:
        return True
    return RANK.get(target, 0) > RANK.get(current, 0)


def sync(session, source: MessageSource, *, source_name: str = "gmail") -> SyncReport:
    state = session.scalar(select(SyncState).where(SyncState.source == source_name))
    if state is None:
        state = SyncState(source=source_name)
        session.add(state)
        session.flush()

    messages, cursor = source.fetch(state.cursor)
    report = SyncReport(cursor=cursor)

    for message in messages:
        report.seen += 1
        link, outcome = apply_message(session, message)
        if outcome == "duplicate":
            report.duplicates += 1
            continue
        if outcome == "orphan":
            report.orphans += 1
        else:
            report.linked += 1
        if outcome == "moved" and link is not None:
            application = session.get(Application, link.application_id)
            report.moved.append(f"{application.job.company.name} — {application.status.value}")
        if link is not None and link.classification in {"offer", "assessment", "interview_invite"}:
            when = f" (due {link.deadline_at:%d %b %H:%M})" if link.deadline_at else ""
            report.urgent.append(f"{link.classification}: {link.subject}{when}")

    state.cursor = cursor or state.cursor
    state.last_synced_at = utcnow()
    session.commit()
    return report


def ghost_stale(session, *, days: int = GHOST_AFTER_DAYS) -> list[int]:
    """Silence is an outcome. Record it, so the pipeline is not 200 rows of hope."""
    cutoff = utcnow() - dt.timedelta(days=days)
    stale = session.scalars(
        select(Application).where(
            Application.status.in_({Status.applied, Status.acknowledged}),
            Application.last_status_change_at < cutoff,
        )
    )
    ghosted = []
    for application in list(stale):
        set_status(session, application, Status.ghosted, source="ghost-sweep",
                   note=f"no reply in {days} days")
        ghosted.append(application.id)
    return ghosted


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except Exception:
        return ""


def plain_text(payload: dict) -> str:
    """Depth-first walk for the text/plain part, falling back to any text."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])
    for part in payload.get("parts") or []:
        found = plain_text(part)
        if found:
            return found
    if mime.startswith("text/") and body.get("data"):
        return _decode(body["data"])
    return ""


def to_message(raw: dict) -> Message:
    headers = {h["name"].casefold(): h["value"] for h in
               (raw.get("payload", {}).get("headers") or [])}
    received = None
    if headers.get("date"):
        try:
            received = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError):
            received = None
    return Message(
        id=raw["id"],
        thread_id=raw.get("threadId", ""),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        body=plain_text(raw.get("payload", {})) or raw.get("snippet", ""),
        received_at=received,
    )


class GmailSource:
    """Reads Gmail through the official client, read-only.

    Kept to the two calls it needs so a change in Google's client surface is a
    small, obvious fix rather than a rewrite.
    """

    def __init__(self, service, *, query: str = "-in:chats -category:promotions",
                 max_results: int = 100):
        self.service = service
        self.query = query
        self.max_results = max_results

    def fetch(self, cursor: str | None) -> tuple[list[Message], str | None]:
        users = self.service.users()
        if cursor:
            ids = self._ids_since(users, cursor)
        else:
            listing = users.messages().list(
                userId="me", q=self.query, maxResults=self.max_results
            ).execute()
            ids = [m["id"] for m in listing.get("messages", [])]

        messages = [
            to_message(users.messages().get(userId="me", id=mid, format="full").execute())
            for mid in ids
        ]
        profile = users.getProfile(userId="me").execute()
        return messages, str(profile.get("historyId") or cursor or "")

    def _ids_since(self, users, cursor: str) -> list[str]:
        ids: list[str] = []
        page = None
        while True:
            response = users.history().list(
                userId="me", startHistoryId=cursor, historyTypes=["messageAdded"],
                pageToken=page,
            ).execute()
            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    ids.append(added["message"]["id"])
            page = response.get("nextPageToken")
            if not page:
                return list(dict.fromkeys(ids))


def build_gmail_service(token_path: str = "data/gmail_token.json",
                        credentials_path: str = "credentials.json"):
    """OAuth once, refresh thereafter. Read-only scope, never write."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from pathlib import Path

    token = Path(token_path)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)
