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
    OPEN_STATUSES, Application, AtsAccount, Company, EmailLink, Job, Status,
    make_engine, make_session_factory, utcnow,
)
from api import autofill, vault
from api.autofill import FieldSpec
from api.schemas import (
    AccountIn, AccountOut, ApplicationPatch, AutofillLog, CaptureResult,
    DigestOut, EmailLinkOut, EmailLinkPatch, FillOut, JobCapture, JobOut,
    ResolveRequest, ResolveResponse, ScoreOut, Stats, SyncOut, TailorOut,
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


# ---------------------------------------------------------------------------
# Autofill
# ---------------------------------------------------------------------------

_profile_cache: dict = {}


def load_profile_cached(path: str = "profile.yaml"):
    """Reload when the file changes, so editing the fact bank takes effect
    without restarting the server."""
    from pathlib import Path

    from resume.loader import load_profile

    stamp = Path(path).stat().st_mtime
    if _profile_cache.get("stamp") != stamp:
        _profile_cache.update(stamp=stamp, profile=load_profile(path))
    return _profile_cache["profile"]


@app.post("/autofill/resolve", response_model=ResolveResponse)
def resolve_fields(request: ResolveRequest) -> ResolveResponse:
    """Say what belongs in each field the extension found.

    Nothing here writes to a page — the content script does that, and only
    after showing you what it touched.
    """
    profile = load_profile_cached()
    specs = [
        FieldSpec(
            id=f.id,
            label=f.label,
            type=f.type,
            options=tuple(f.options),
            required=f.required,
            automation_id=f.automation_id,
        )
        for f in request.fields
    ]
    fills = autofill.resolve(profile, specs)
    resume_url = f"/resume/{request.shape}.pdf" if request.shape else None
    return ResolveResponse(
        fills=[FillOut(**vars(f)) for f in fills],
        filled=sum(1 for f in fills if f.action in {"fill", "select"}),
        skipped=sum(1 for f in fills if f.action == "skip"),
        unmatched=sum(1 for f in fills if f.action == "unmatched"),
        resume_url=resume_url,
    )


@app.get("/resume/{shape}.pdf")
def resume_pdf(shape: str):
    """Render (and cache) the resume for a shape, so the extension can attach it."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    from resume.schema import Shape
    from resume.select import select
    from resume.typst_render import render_pdf
    from resume.verify import check_selection

    try:
        target = Shape(shape)
    except ValueError:
        raise HTTPException(404, f"unknown shape {shape!r}")

    profile = load_profile_cached()
    selection = select(profile, target)
    problems = check_selection(profile, selection)
    if problems:
        raise HTTPException(409, f"resume failed traceability: {problems[0]}")

    name = profile.identity.name.replace(" ", "_")
    out = Path("data/resumes") / f"{name}_{target.value}.pdf"
    render_pdf(profile, selection, out)
    return FileResponse(out, media_type="application/pdf", filename=out.name)


# ---------------------------------------------------------------------------
# Per-tenant ATS accounts
# ---------------------------------------------------------------------------

@app.get("/ats-accounts", response_model=list[AccountOut])
def list_accounts(
    tenant: str | None = None, session: Session = Depends(get_session)
) -> list[AccountOut]:
    stmt = select(AtsAccount)
    if tenant:
        stmt = stmt.where(AtsAccount.tenant == tenant)
    return list(session.scalars(stmt))


@app.post("/ats-accounts", response_model=AccountOut)
def upsert_account(
    payload: AccountIn, session: Session = Depends(get_session)
) -> AccountOut:
    """Remember a tenant login. The password goes to the OS keychain only."""
    if not vault.available():
        raise HTTPException(503, "no OS keychain available on this machine")

    ref = vault.secret_ref(payload.ats_type, payload.tenant, payload.username)
    account = session.scalar(select(AtsAccount).where(AtsAccount.secret_ref == ref))
    if account is None:
        company = (
            store.get_or_create_company(session, payload.company) if payload.company else None
        )
        account = AtsAccount(
            company_id=company.id if company else None,
            ats_type=payload.ats_type,
            tenant=payload.tenant,
            username=payload.username,
            secret_ref=ref,
            notes=payload.notes,
        )
        session.add(account)
    vault.store(ref, payload.password)
    session.commit()
    return account


@app.get("/ats-accounts/{account_id}/credentials")
def account_credentials(account_id: int, session: Session = Depends(get_session)) -> dict:
    """Hand back one tenant's login, on explicit request, to localhost only."""
    account = session.get(AtsAccount, account_id)
    if account is None:
        raise HTTPException(404, "no such account")
    password = vault.fetch(account.secret_ref)
    if password is None:
        raise HTTPException(404, "no password stored for this account")
    return {"username": account.username, "password": password}


@app.post("/autofill/log")
def log_autofill(payload: AutofillLog, session: Session = Depends(get_session)) -> dict:
    """Audit trail: exactly which values this system put on a form."""
    application = session.get(Application, payload.application_id)
    if application is None:
        raise HTTPException(404, "no such application")
    store.log(
        session, application, "autofilled", payload.ats,
        {"step": payload.step, "filled": payload.filled, "corrected": payload.corrected},
    )
    session.commit()
    return {"ok": True, "recorded": len(payload.filled)}


@app.post("/applications/{application_id}/submitted", response_model=JobOut)
def mark_submitted(
    application_id: int, session: Session = Depends(get_session)
) -> JobOut:
    """Called when the extension sees a confirmation page.

    This is why the tracker stays accurate: the status moves because the form
    was actually submitted, not because someone remembered to click a button.
    """
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "no such application")
    store.set_status(session, application, Status.applied, source="confirmation-page")
    return to_job_out(application.job)


# ---------------------------------------------------------------------------
# Scoring and tailoring
# ---------------------------------------------------------------------------

def _score_job(job: Job):
    """Analyse a stored description and score it against the fact bank."""
    from tailor.pipeline import analyse_and_score

    profile = load_profile_cached()
    jd, result = analyse_and_score(profile, job)
    return profile, jd, result


def _score_out(job: Job, jd, result) -> ScoreOut:
    return ScoreOut(
        job_id=job.id,
        total=result.total,
        shape=result.shape.value,
        shape_confidence=jd.shape_confidence,
        passes=result.passes,
        reason=result.reason,
        required_matched=result.required_matched,
        gaps=result.gaps,
        preferred_matched=result.preferred_matched,
        years_required=result.years_required,
        years_have=result.years_have,
        years_penalty=result.years_penalty,
    )


@app.post("/jobs/{job_id}/score", response_model=ScoreOut)
def score_job(job_id: int, session: Session = Depends(get_session)) -> ScoreOut:
    """Score a captured job. Records the verdict and why, including a skip."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if not (job.description_raw or "").strip():
        raise HTTPException(409, "no description captured for this job")

    from tailor.pipeline import record_score

    _profile, jd, result = _score_job(job)
    record_score(session, job, result)
    return _score_out(job, jd, result)


@app.post("/jobs/{job_id}/tailor", response_model=TailorOut)
def tailor_job(
    job_id: int,
    force: bool = False,
    use_model: bool = False,
    session: Session = Depends(get_session),
) -> TailorOut:
    """Score, gate, then render a resume aimed at this posting."""
    from tailor.pipeline import record_score, tailor as run_tailor

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if not (job.description_raw or "").strip():
        raise HTTPException(409, "no description captured for this job")

    profile, jd, result = _score_job(job)
    if not result.passes and not force:
        record_score(session, job, result)
        return TailorOut(job_id=job.id, score=_score_out(job, jd, result), tailored=False,
                         note=f"below threshold: {result.reason}")

    job.application.match_score = result.total
    job.application.shape = result.shape.value
    outcome = run_tailor(session, profile, job, jd, result, use_model=use_model)
    if not outcome.tailored:
        raise HTTPException(409, outcome.note)

    return TailorOut(
        job_id=job.id, score=_score_out(job, jd, result), tailored=True,
        resume_path=outcome.resume_path, docx_path=outcome.docx_path,
        rephrased=outcome.rephrased, note=outcome.note,
    )


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

@app.post("/inbox/sync", response_model=SyncOut)
def inbox_sync(session: Session = Depends(get_session)) -> SyncOut:
    """Pull new mail and move applications. Read-only against Gmail."""
    from inbox.sync import GmailSource, build_gmail_service, sync as run_sync

    try:
        service = build_gmail_service()
    except Exception as error:
        raise HTTPException(
            503,
            f"Gmail is not connected ({type(error).__name__}). Run "
            "`python -m inbox.run --sync` once to complete the OAuth consent.",
        )
    report = run_sync(session, GmailSource(service))
    return SyncOut(
        seen=report.seen, linked=report.linked, orphans=report.orphans,
        duplicates=report.duplicates, moved=report.moved, urgent=report.urgent,
    )


@app.post("/inbox/ghost-sweep")
def inbox_ghost_sweep(days: int = 21, session: Session = Depends(get_session)) -> dict:
    """Silence is an outcome; record it rather than leaving rows hopeful."""
    from inbox.sync import ghost_stale

    ghosted = ghost_stale(session, days=days)
    return {"ghosted": len(ghosted), "application_ids": ghosted, "days": days}


@app.get("/inbox/digest", response_model=DigestOut)
def inbox_digest(hours: int = 24, session: Session = Depends(get_session)) -> DigestOut:
    from inbox import digest as digest_module

    built = digest_module.build(session, since_hours=hours)
    as_dicts = lambda items: [
        {"text": i.text, "detail": i.detail,
         "when": i.when.isoformat() if i.when else None} for i in items
    ]
    return DigestOut(
        generated_at=built.generated_at,
        urgent=as_dicts(built.urgent),
        ready_to_apply=as_dicts(built.ready_to_apply),
        moved=as_dicts(built.moved),
        going_quiet=as_dicts(built.going_quiet),
        orphans=as_dicts(built.orphans),
        pipeline=built.pipeline,
        text=digest_module.render(built),
    )


@app.get("/email-links", response_model=list[EmailLinkOut])
def list_email_links(
    orphans_only: bool = False,
    unreviewed_only: bool = False,
    limit: int = Query(100, le=500),
    session: Session = Depends(get_session),
) -> list[EmailLinkOut]:
    stmt = select(EmailLink)
    if orphans_only:
        stmt = stmt.where(EmailLink.application_id.is_(None))
    if unreviewed_only:
        stmt = stmt.where(EmailLink.reviewed.is_(False))
    stmt = stmt.order_by(EmailLink.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


@app.patch("/email-links/{link_id}", response_model=EmailLinkOut)
def patch_email_link(
    link_id: int, patch: EmailLinkPatch, session: Session = Depends(get_session)
) -> EmailLinkOut:
    """Place an orphan by hand, and optionally let it move the application.

    The system will not guess which application an ambiguous email belongs to;
    this is where you tell it, once.
    """
    from inbox.sync import IMPLIES, _should_move

    link = session.get(EmailLink, link_id)
    if link is None:
        raise HTTPException(404, "no such email link")

    if patch.application_id is not None:
        application = session.get(Application, patch.application_id)
        if application is None:
            raise HTTPException(404, "no such application")
        link.application_id = application.id
        store.log(session, application, "email_placed_by_hand", "api",
                  {"subject": link.subject, "classification": link.classification})
        if patch.apply_status:
            target = IMPLIES.get(link.classification)
            if target is not None and _should_move(application.status, target):
                store.set_status(session, application, target, source="gmail-manual",
                                 note=link.subject)
    if patch.reviewed is not None:
        link.reviewed = patch.reviewed

    session.commit()
    return link
