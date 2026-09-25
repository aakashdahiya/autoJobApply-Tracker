"""FastAPI service. Bind to localhost only — there is no auth yet.

    uvicorn api.main:app --host 127.0.0.1 --port 8765 --reload
"""

from __future__ import annotations

import datetime as dt

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api import store
from api.db import (
    OPEN_STATUSES, Application, Company, Job, Status, make_engine,
    make_session_factory, utcnow,
)
from api.schemas import (
    ApplicationPatch, CaptureResult, JobCapture, JobOut, Stats,
)

engine = make_engine()
SessionLocal = make_session_factory(engine)

app = FastAPI(title="autoJobApply Tracker", version="0.1.0")

# The extension is the only client. Chrome sends `chrome-extension://<id>`.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-p]+$",
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def to_job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        company=job.company.name,
        locations=job.locations or [],
        remote_type=job.remote_type,
        apply_url=job.apply_url,
        source=job.source,
        salary_raw=job.salary_raw,
        discovered_at=job.discovered_at,
        is_open=job.is_open,
        application=job.application,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/jobs", response_model=CaptureResult)
def capture(payload: JobCapture, session: Session = Depends(get_session)) -> CaptureResult:
    """Save a posting. Idempotent: the same job captured twice updates, never duplicates."""
    warning = None
    existing = store.recent_duplicate(session, payload.company, payload.title)
    if existing is not None:
        warning = (
            f"Already applied to this role on {existing.applied_at:%d %b %Y} "
            f"(status: {existing.status.value})"
        )

    job, _application, created = store.capture_job(session, payload.model_dump())
    return CaptureResult(created=created, job=to_job_out(job), duplicate_warning=warning)


@app.get("/jobs", response_model=list[JobOut])
def list_jobs(
    session: Session = Depends(get_session),
    status: Status | None = None,
    open_only: bool = False,
    company: str | None = None,
    search: str | None = None,
    limit: int = Query(100, le=500),
) -> list[JobOut]:
    stmt = select(Job).join(Application).join(Company)
    if status is not None:
        stmt = stmt.where(Application.status == status)
    if open_only:
        stmt = stmt.where(Application.status.in_(OPEN_STATUSES))
    if company:
        stmt = stmt.where(Company.name_norm.contains(company.casefold()))
    if search:
        stmt = stmt.where(Job.title_norm.contains(search.casefold()))
    stmt = stmt.order_by(Job.discovered_at.desc()).limit(limit)
    return [to_job_out(job) for job in session.scalars(stmt)]


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return to_job_out(job)


@app.patch("/applications/{application_id}", response_model=JobOut)
def patch_application(
    application_id: int, patch: ApplicationPatch, session: Session = Depends(get_session)
) -> JobOut:
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "no such application")

    fields = patch.model_dump(exclude_unset=True, exclude={"status"})
    if fields:
        for key, value in fields.items():
            setattr(application, key, value)
        store.log(session, application, "edited", "api", fields)
        session.commit()
    if patch.status is not None:
        store.set_status(session, application, patch.status, source="api")

    session.refresh(application)
    return to_job_out(application.job)


@app.get("/stats", response_model=Stats)
def stats(session: Session = Depends(get_session)) -> Stats:
    rows = session.execute(
        select(Application.status, func.count(Application.id)).group_by(Application.status)
    ).all()
    week_ago = utcnow() - dt.timedelta(days=7)
    applied = session.scalar(
        select(func.count(Application.id)).where(Application.applied_at >= week_ago)
    )
    return Stats(
        total_jobs=session.scalar(select(func.count(Job.id))) or 0,
        by_status={status.value: count for status, count in rows},
        applied_this_week=applied or 0,
    )
