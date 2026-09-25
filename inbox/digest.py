"""The one message a day, plus what should not wait for it.

Ordered by what it costs to miss. An assessment with a 72-hour window is the
single most expensive thing to overlook, so it leads and it also justifies an
immediate push; a rejection can wait until morning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select

from api.db import Application, Company, EmailLink, Job, Status, utcnow

URGENT_KINDS = ("offer", "assessment", "interview_invite")
GOING_QUIET_DAYS = 14


@dataclass
class Item:
    text: str
    detail: str = ""
    when: dt.datetime | None = None


@dataclass
class Digest:
    generated_at: dt.datetime
    urgent: list[Item] = field(default_factory=list)
    moved: list[Item] = field(default_factory=list)
    orphans: list[Item] = field(default_factory=list)
    going_quiet: list[Item] = field(default_factory=list)
    ready_to_apply: list[Item] = field(default_factory=list)
    pipeline: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.urgent or self.moved or self.orphans
                    or self.going_quiet or self.ready_to_apply)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def build(session, *, since_hours: int = 24, now: dt.datetime | None = None) -> Digest:
    now = now or utcnow()
    since = now - dt.timedelta(hours=since_hours)
    digest = Digest(generated_at=now)

    urgent_links = session.scalars(
        select(EmailLink)
        .where(EmailLink.classification.in_(URGENT_KINDS), EmailLink.reviewed.is_(False))
        .order_by(EmailLink.deadline_at.is_(None), EmailLink.deadline_at)
    )
    for link in urgent_links:
        deadline = _aware(link.deadline_at)
        detail = link.sender or ""
        if deadline:
            hours = (deadline - now).total_seconds() / 3600
            detail = (
                f"due {deadline:%a %d %b %H:%M} — {hours:.0f}h left"
                if hours >= 0 else f"deadline passed {deadline:%d %b}"
            )
        digest.urgent.append(
            Item(f"[{link.classification}] {link.subject}", detail, deadline)
        )

    recent = session.scalars(
        select(EmailLink)
        .where(EmailLink.created_at >= since.replace(tzinfo=None))
        .order_by(EmailLink.created_at.desc())
    )
    for link in recent:
        if link.application_id is None:
            digest.orphans.append(
                Item(link.subject or "(no subject)",
                     f"{link.sender} — {(link.extracted or {}).get('match_reason', '')}")
            )
        elif link.classification in {"rejection", "acknowledgement"}:
            application = session.get(Application, link.application_id)
            digest.moved.append(
                Item(f"{application.job.company.name} — {application.status.value}",
                     link.subject or "")
            )

    quiet_cutoff = now - dt.timedelta(days=GOING_QUIET_DAYS)
    quiet = session.scalars(
        select(Application)
        .join(Job).join(Company)
        .where(Application.status.in_({Status.applied, Status.acknowledged}),
               Application.last_status_change_at < quiet_cutoff.replace(tzinfo=None))
        .order_by(Application.last_status_change_at)
    )
    for application in quiet:
        changed = _aware(application.last_status_change_at)
        days = (now - changed).days if changed else 0
        digest.going_quiet.append(
            Item(f"{application.job.company.name} — {application.job.title}",
                 f"{days} days since {application.status.value}")
        )

    ready = session.scalars(
        select(Application).join(Job).join(Company)
        .where(Application.status.in_({Status.tailored, Status.ready}))
        .order_by(Application.match_score.desc())
    )
    for application in ready:
        digest.ready_to_apply.append(
            Item(f"{application.job.company.name} — {application.job.title}",
                 f"score {application.match_score:.0f}" if application.match_score else "")
        )

    rows = session.execute(
        select(Application.status, __import__("sqlalchemy").func.count(Application.id))
        .group_by(Application.status)
    ).all()
    digest.pipeline = {status.value: count for status, count in rows}
    return digest


def render(digest: Digest) -> str:
    lines = [f"Job search digest — {digest.generated_at:%a %d %b %Y, %H:%M}", ""]

    def section(title: str, items: list[Item], empty: str | None = None) -> None:
        if not items:
            if empty:
                lines.extend([title, f"  {empty}", ""])
            return
        lines.append(title)
        for item in items:
            lines.append(f"  • {item.text}")
            if item.detail:
                lines.append(f"    {item.detail}")
        lines.append("")

    section("NEEDS YOU NOW", digest.urgent)
    section("READY TO APPLY", digest.ready_to_apply)
    section("MOVED BY EMAIL", digest.moved)
    section("GOING QUIET", digest.going_quiet)
    section("COULD NOT PLACE (tell me which application)", digest.orphans)

    if digest.pipeline:
        summary = ", ".join(f"{count} {status}" for status, count in sorted(digest.pipeline.items()))
        lines.extend(["PIPELINE", f"  {summary}", ""])
    if digest.is_empty:
        lines.append("Nothing needs you today.")
    return "\n".join(lines).rstrip() + "\n"
