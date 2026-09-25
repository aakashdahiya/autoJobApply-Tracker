"""Attach an email to the application it is about — or admit it cannot.

Guessing here is worse than not matching. A misattached rejection closes the
wrong application and hides a live one, so anything ambiguous becomes an
orphan for the digest to surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.db import Application, Company, Job, Status
from api.normalise import company_norm, title_norm

# Mail from the ATS itself carries no employer signal in the domain.
ATS_DOMAINS = {
    "greenhouse.io", "us.greenhouse-mail.io", "greenhouse-mail.io", "lever.co",
    "hire.lever.co", "ashbyhq.com", "myworkday.com", "myworkdayjobs.com",
    "workday.com", "smartrecruiters.com", "workable.com", "icims.com",
    "taleo.net", "successfactors.com", "bamboohr.com", "recruitee.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "gmail.com", "googlemail.com",
}

THREAD_POINTS = 100.0
DOMAIN_POINTS = 50.0
NAME_POINTS = 30.0
TITLE_POINTS = 25.0
RECENCY_POINTS = 5.0
MIN_POINTS = 30.0  # below this, an orphan


@dataclass
class Match:
    application_id: int | None = None
    score: float = 0.0
    reason: str = ""
    ambiguous: bool = False


def sender_domain(sender: str) -> str:
    """The domain out of `Name <a@b.com>`, minus any subdomain."""
    match = re.search(r"[\w.+-]+@([\w.-]+)", sender or "")
    if not match:
        return ""
    host = match.group(1).casefold().strip(".")
    parts = host.split(".")
    # Keep two labels, or three for the likes of `.co.uk`.
    if len(parts) > 2 and parts[-2] in {"co", "com", "ac", "gov", "org"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_stem(domain: str) -> str:
    return domain.split(".")[0] if domain else ""


def candidates(session, *, statuses: set[Status] | None = None) -> list[Application]:
    from sqlalchemy import select as sa_select

    stmt = sa_select(Application).join(Job).join(Company)
    if statuses:
        stmt = stmt.where(Application.status.in_(statuses))
    return list(session.scalars(stmt))


def match_email(
    session,
    *,
    sender: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> Match:
    from sqlalchemy import select as sa_select

    from api.db import EmailLink

    if thread_id:
        linked = session.scalar(
            sa_select(EmailLink)
            .where(EmailLink.gmail_thread_id == thread_id)
            .where(EmailLink.application_id.is_not(None))
        )
        if linked:
            return Match(linked.application_id, THREAD_POINTS, "same thread as an earlier match")

    domain = sender_domain(sender)
    stem = domain_stem(domain)
    text = re.sub(r"\s+", " ", f"{subject or ''} {(body or '')[:3000]}").casefold()

    scored: list[tuple[float, list[str], Application]] = []
    for application in candidates(session):
        company = application.job.company
        points, why = 0.0, []

        if domain and domain not in ATS_DOMAINS:
            if company.domain and company.domain.casefold().endswith(domain):
                points += DOMAIN_POINTS
                why.append("sender domain")
            elif stem and stem == company.name_norm.replace(" ", ""):
                points += DOMAIN_POINTS
                why.append("sender domain")

        if company.name_norm and company.name_norm in text:
            points += NAME_POINTS
            why.append("company named")

        normalised_title = title_norm(application.job.title)
        if normalised_title and normalised_title in title_norm(text):
            points += TITLE_POINTS
            why.append("title named")

        if application.status in {Status.applied, Status.acknowledged, Status.screening,
                                  Status.assessment, Status.interview}:
            points += RECENCY_POINTS

        if points:
            scored.append((points, why, application))

    if not scored:
        return Match(reason="no application matched this sender or subject")

    scored.sort(key=lambda row: row[0], reverse=True)
    best_points, best_why, best = scored[0]

    if best_points < MIN_POINTS:
        return Match(reason=f"weak signal ({', '.join(best_why) or 'none'})")

    # Two applications at one company, nothing to separate them: do not pick.
    if len(scored) > 1 and scored[1][0] == best_points:
        return Match(
            score=best_points,
            ambiguous=True,
            reason=f"{sum(1 for row in scored if row[0] == best_points)} applications match equally",
        )

    return Match(best.id, best_points, ", ".join(best_why))
