"""Capture and update logic. Every write is idempotent."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from api import normalise
from api.db import Application, Company, Event, Job, Status, utcnow


def get_or_create_company(session: Session, name: str, **fields) -> Company:
    norm = normalise.company_norm(name)
    company = session.scalar(select(Company).where(Company.name_norm == norm))
    if company is None:
        company = Company(name=name.strip(), name_norm=norm, **fields)
        session.add(company)
        session.flush()
    else:
        for key, value in fields.items():  # fill blanks, never overwrite
            if value and getattr(company, key, None) in (None, "", False):
                setattr(company, key, value)
    return company


def _merge_locations(existing: list[str], incoming: list[str]) -> list[str]:
    """Union, canonical, stable order — one job, several cities."""
    merged = list(existing or [])
    for raw in incoming:
        canonical = normalise.location_norm(raw)
        if canonical and canonical not in merged:
            merged.append(canonical)
    return merged


def capture_job(session: Session, payload: dict) -> tuple[Job, Application, bool]:
    """Upsert a job by dedupe key. Returns (job, application, created)."""
    company = get_or_create_company(
        session,
        payload["company"],
        ats_type=payload.get("ats_type"),
        careers_url=payload.get("careers_url"),
    )
    key = normalise.dedupe_key(payload["company"], payload["title"])
    locations = payload.get("locations") or (
        [payload["location"]] if payload.get("location") else []
    )
    description = payload.get("description") or ""

    job = session.scalar(select(Job).where(Job.dedupe_key == key))
    created = job is None

    if created:
        job = Job(
            company_id=company.id,
            title=payload["title"].strip(),
            title_norm=normalise.title_norm(payload["title"]),
            locations=_merge_locations([], locations),
            remote_type=normalise.remote_type(" ".join(locations), description),
            description_raw=description or None,
            description_hash=normalise.description_hash(description) if description else None,
            apply_url=payload["apply_url"],
            canonical_url=payload.get("canonical_url") or payload["apply_url"],
            source=payload.get("source", "extension"),
            salary_raw=payload.get("salary"),
            dedupe_key=key,
        )
        session.add(job)
        session.flush()
        application = Application(job_id=job.id, status=Status.discovered)
        session.add(application)
        session.flush()
        log(session, application, "job_captured", payload.get("source", "extension"),
            {"apply_url": job.apply_url, "locations": job.locations})
    else:
        merged = _merge_locations(job.locations, locations)
        if merged != job.locations:
            job.locations = merged
        if description and not job.description_raw:
            job.description_raw = description
            job.description_hash = normalise.description_hash(description)
        application = job.application
        log(session, application, "job_seen_again", payload.get("source", "extension"),
            {"apply_url": payload["apply_url"]})

    session.commit()
    return job, application, created


def set_status(
    session: Session, application: Application, status: Status, *, source: str = "api",
    note: str | None = None,
) -> Application:
    if application.status is status:
        return application
    previous = application.status
    application.status = status
    application.last_status_change_at = utcnow()
    if status is Status.applied and application.applied_at is None:
        application.applied_at = utcnow()
    log(session, application, "status_changed", source,
        {"from": previous.value, "to": status.value, "note": note})
    session.commit()
    return application


def log(session: Session, application: Application, kind: str, source: str,
        payload: dict | None = None) -> Event:
    event = Event(application_id=application.id, kind=kind, source=source, payload=payload)
    session.add(event)
    return event


def recent_duplicate(session: Session, company: str, title: str, *, within_days: int = 60):
    """Have we already applied to this role recently?

    The guard against the worst failure mode of a high-volume pipeline:
    applying twice to the same job at the same company.
    """
    key = normalise.dedupe_key(company, title)
    job = session.scalar(select(Job).where(Job.dedupe_key == key))
    if job is None or job.application is None:
        return None
    app = job.application
    if app.applied_at is None:
        return None
    cutoff = utcnow() - dt.timedelta(days=within_days)
    applied_at = app.applied_at
    if applied_at.tzinfo is None:
        applied_at = applied_at.replace(tzinfo=dt.timezone.utc)
    return app if applied_at >= cutoff else None
