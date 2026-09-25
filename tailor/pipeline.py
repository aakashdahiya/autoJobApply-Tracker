"""Score and tailor a stored job. Shared by the API and the nightly crawl.

Both entry points have to record the same things — the verdict, the reason for
a skip, the resume path — so they share one implementation rather than two that
drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from api.db import Job, Status
from api.store import log, set_status
from resume.schema import Profile
from tailor.cache import analyse_cached
from tailor.jd import JobDescription
from tailor.score import Score, score as score_profile


@dataclass
class TailorOutcome:
    tailored: bool
    resume_path: str | None = None
    docx_path: str | None = None
    rephrased: int = 0
    note: str = ""


def analyse_and_score(profile: Profile, job: Job) -> tuple[JobDescription, Score]:
    jd = analyse_cached(job.description_raw or "", job.description_hash)
    return jd, score_profile(profile, jd)


def record_score(session, job: Job, result: Score) -> None:
    """Save the verdict, and move the application to `scored` or `skipped`.

    A skip records why. Without that, the same posting is reconsidered from
    scratch every time it reappears from another source.
    """
    application = job.application
    application.match_score = result.total
    application.shape = result.shape.value
    log(session, application, "scored", "pipeline", {
        "total": result.total, "shape": result.shape.value,
        "reason": result.reason, "gaps": result.gaps,
    })
    if not result.passes:
        set_status(session, application, Status.skipped, source="score-gate",
                   note=result.reason)
    elif application.status is Status.discovered:
        set_status(session, application, Status.scored, source="score-gate")
    session.commit()


def tailor(
    session,
    profile: Profile,
    job: Job,
    jd: JobDescription,
    result: Score,
    *,
    use_model: bool = False,
    out_dir: Path = Path("data/resumes"),
) -> TailorOutcome:
    from resume.docx_render import render_docx
    from resume.select import select
    from resume.typst_render import render_pdf
    from resume.verify import check_selection
    from tailor.rephrase import rephrase

    selection = select(profile, result.shape)
    note, rephrased = "", 0
    if use_model:
        rewritten = rephrase(
            profile, selection,
            target_terms=sorted(jd.required | jd.preferred),
            title=job.title,
        )
        selection, note, rephrased = rewritten.selection, rewritten.note, rewritten.changed

    problems = check_selection(profile, selection, strict=not use_model)
    if problems:
        return TailorOutcome(False, note=f"traceability failed: {problems[0]}")

    stem = (
        f"{profile.identity.name.replace(' ', '_')}_"
        f"{job.company.name_norm.replace(' ', '_')}_{result.shape.value}"
    )
    pdf = render_pdf(profile, selection, out_dir / f"{stem}.pdf")
    docx = render_docx(profile, selection, out_dir / f"{stem}.docx")

    application = job.application
    application.resume_path = str(pdf)
    log(session, application, "tailored", "pipeline", {
        "shape": result.shape.value, "score": result.total,
        "rephrased": rephrased, "resume": str(pdf),
    })
    if application.status in {Status.discovered, Status.scored}:
        set_status(session, application, Status.tailored, source="pipeline")
    session.commit()
    return TailorOutcome(True, str(pdf), str(docx), rephrased, note)
