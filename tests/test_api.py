"""The capture API. Every write must be safe to repeat."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.db import Application, Status, make_engine, make_session_factory, utcnow


@pytest.fixture
def client():
    engine = make_engine("sqlite://")
    factory = make_session_factory(engine)

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_session] = override
    with TestClient(main.app) as c:
        c.session_factory = factory
        yield c
    main.app.dependency_overrides.clear()


def post_job(client, **overrides):
    payload = {
        "title": "Senior Python Engineer",
        "company": "Cohere",
        "apply_url": "https://jobs.cohere.com/1",
        "location": "Toronto, ON",
    }
    payload.update(overrides)
    return client.post("/jobs", json=payload)


def test_capture_creates_a_job_and_an_application(client):
    body = post_job(client).json()
    assert body["created"] is True
    assert body["job"]["company"] == "Cohere"
    assert body["job"]["locations"] == ["toronto, on"]
    assert body["job"]["application"]["status"] == "discovered"


def test_capturing_the_same_job_twice_does_not_duplicate(client):
    post_job(client)
    second = post_job(client, apply_url="https://linkedin.com/jobs/999")
    assert second.json()["created"] is False
    assert len(client.get("/jobs").json()) == 1


def test_multi_city_requisition_collapses_to_one_job(client):
    """The failure this design exists to prevent."""
    post_job(client, location="Toronto, ON")
    post_job(client, location="Vancouver, BC")
    post_job(client, location="Montréal")

    jobs = client.get("/jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["locations"] == ["toronto, on", "vancouver, bc", "montreal, qc"]


def test_title_and_company_variants_are_one_job(client):
    post_job(client, title="Sr. Python Eng", company="Cohere Inc.")
    post_job(client, title="Senior Python Engineer", company="cohere")
    assert len(client.get("/jobs").json()) == 1


def test_different_roles_at_one_company_stay_separate(client):
    post_job(client, title="Senior Python Engineer")
    post_job(client, title="Data Engineer")
    assert len(client.get("/jobs").json()) == 2


def test_description_is_kept_from_whichever_source_had_it(client):
    """LinkedIn often has no description; the company board does."""
    post_job(client)
    post_job(client, description="We are hiring a Python engineer.", source="greenhouse")
    job_id = client.get("/jobs").json()[0]["id"]
    session = client.session_factory()
    from api.db import Job

    assert "Python engineer" in session.get(Job, job_id).description_raw
    session.close()


def test_status_patch_records_applied_at_and_an_event(client):
    app_id = post_job(client).json()["job"]["application"]["id"]
    body = client.patch(f"/applications/{app_id}", json={"status": "applied"}).json()
    assert body["application"]["status"] == "applied"
    assert body["application"]["applied_at"] is not None

    session = client.session_factory()
    events = session.get(Application, app_id).events
    assert any(e.kind == "status_changed" for e in events)
    session.close()


def test_notes_patch_does_not_disturb_status(client):
    app_id = post_job(client).json()["job"]["application"]["id"]
    body = client.patch(f"/applications/{app_id}", json={"notes": "referred by Sam"}).json()
    assert body["application"]["notes"] == "referred by Sam"
    assert body["application"]["status"] == "discovered"


def test_recapturing_an_applied_job_warns_about_double_applying(client):
    """The worst failure mode of a high-volume pipeline, caught at capture."""
    app_id = post_job(client).json()["job"]["application"]["id"]
    client.patch(f"/applications/{app_id}", json={"status": "applied"})

    warning = post_job(client).json()["duplicate_warning"]
    assert warning and "Already applied" in warning


def test_no_warning_before_you_have_applied(client):
    post_job(client)
    assert post_job(client).json()["duplicate_warning"] is None


def test_old_applications_do_not_warn(client):
    """A role you applied to a year ago is worth applying to again."""
    app_id = post_job(client).json()["job"]["application"]["id"]
    session = client.session_factory()
    application = session.get(Application, app_id)
    application.status = Status.applied
    application.applied_at = utcnow() - dt.timedelta(days=400)
    session.commit()
    session.close()

    assert post_job(client).json()["duplicate_warning"] is None


def test_filters(client):
    post_job(client, title="Senior Python Engineer", company="Cohere")
    post_job(client, title="Data Engineer", company="Shopify")
    assert len(client.get("/jobs", params={"company": "cohere"}).json()) == 1
    assert len(client.get("/jobs", params={"search": "data"}).json()) == 1
    assert len(client.get("/jobs", params={"status": "discovered"}).json()) == 2


def test_stats(client):
    app_id = post_job(client).json()["job"]["application"]["id"]
    client.patch(f"/applications/{app_id}", json={"status": "applied"})
    stats = client.get("/stats").json()
    assert stats["total_jobs"] == 1
    assert stats["by_status"] == {"applied": 1}
    assert stats["applied_this_week"] == 1


def test_missing_application_is_404(client):
    assert client.patch("/applications/999", json={"status": "applied"}).status_code == 404


def test_capture_rejects_a_blank_title(client):
    assert post_job(client, title="").status_code == 422


# --- scoring and tailoring --------------------------------------------------

AI_JD = """Senior AI Engineer

What you'll need:
- 2+ years of Python
- LLMs, RAG and embeddings
- FastAPI and Postgres

Nice to have:
- Next.js and React
"""

WRONG_JD = """Staff Platform Engineer

Requirements:
- 10+ years with Java, Scala and Spring Boot
- Kubernetes, Terraform and AWS at scale
"""


def test_scoring_records_the_verdict_and_the_shape(client):
    job_id = post_job(client, description=AI_JD).json()["job"]["id"]
    body = client.post(f"/jobs/{job_id}/score").json()

    assert body["passes"] is True
    assert body["shape"] == "ai_engineer"
    assert {"python", "rag", "embeddings"} <= set(body["required_matched"])
    assert client.get(f"/jobs/{job_id}").json()["application"]["status"] == "scored"


def test_a_poor_match_is_skipped_with_a_reason_recorded(client):
    """A skip has to say why, or you relitigate it every time it reappears."""
    job_id = post_job(client, description=WRONG_JD).json()["job"]["id"]
    body = client.post(f"/jobs/{job_id}/score").json()

    assert body["passes"] is False
    assert body["gaps"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["application"]["status"] == "skipped"

    session = client.session_factory()
    events = session.get(Application, job["application"]["id"]).events
    assert any(e.kind == "scored" and e.payload["gaps"] for e in events)
    session.close()


def test_tailoring_renders_a_resume_and_links_it(client):
    job_id = post_job(client, description=AI_JD).json()["job"]["id"]
    body = client.post(f"/jobs/{job_id}/tailor").json()

    assert body["tailored"] is True
    assert body["resume_path"].endswith(".pdf")
    from pathlib import Path

    assert Path(body["resume_path"]).exists()
    job = client.get(f"/jobs/{job_id}").json()
    assert job["application"]["status"] == "tailored"
    assert job["application"]["resume_path"] == body["resume_path"]


def test_the_gate_stops_tailoring_unless_forced(client):
    job_id = post_job(client, description=WRONG_JD).json()["job"]["id"]
    assert client.post(f"/jobs/{job_id}/tailor").json()["tailored"] is False
    assert client.post(f"/jobs/{job_id}/tailor", params={"force": True}).json()["tailored"] is True


def test_scoring_a_job_with_no_description_is_a_clear_error(client):
    job_id = post_job(client).json()["job"]["id"]
    response = client.post(f"/jobs/{job_id}/score")
    assert response.status_code == 409
    assert "description" in response.json()["detail"]



def test_an_unconfigured_transport_is_reported_as_owed_not_as_nothing(client, monkeypatch):
    """`push_failed: 0` would read as "nothing needed sending", which is the
    opposite of the truth when the transport is simply not set up."""
    import inbox.notify as notify_module
    import inbox.sync as sync_module
    from inbox.sync import Message

    urgent = Message(
        id="m-urgent", sender="ta@cohere.com",
        subject="Your online assessment", body="Complete the assessment within 72 hours.",
    )

    class FakeSource:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, cursor):
            return [urgent], "cursor-1"

    def unconfigured(*args, **kwargs):
        raise notify_module.PushFailed("JOB_TRACKER_NOTIFY=telegram needs TELEGRAM_BOT_TOKEN")

    monkeypatch.setattr(sync_module, "build_gmail_service", lambda *a, **k: object())
    monkeypatch.setattr(sync_module, "GmailSource", FakeSource)
    monkeypatch.setattr(notify_module, "push_pending", unconfigured)

    body = client.post("/inbox/sync").json()

    assert body["seen"] == 1
    assert body["urgent"], "an assessment should have been flagged urgent"
    assert body["pushed"] == 0
    assert body["push_failed"] == 1, "an owed push reported as zero reads as nothing owed"


# --- which resume gets attached ---------------------------------------------

AI_JD_FOR_RESUME = AI_JD


def test_the_jobs_own_resume_is_attached_once_it_is_tailored(client):
    """Attaching the shape-level resume to a posting that has a tailored one is
    the quiet version of applying with the wrong file."""
    job_id = post_job(client, description=AI_JD_FOR_RESUME).json()["job"]["id"]
    tailored = client.post(f"/jobs/{job_id}/tailor").json()
    assert tailored["tailored"] is True

    application_id = client.get(f"/jobs/{job_id}").json()["application"]["id"]
    body = client.post("/autofill/resolve", json={
        "fields": [], "application_id": application_id, "shape": "ai_engineer",
    }).json()

    assert body["resume_url"] == f"/applications/{application_id}/resume.pdf"


def test_an_untailored_application_falls_back_to_the_shape_resume(client):
    job_id = post_job(client, description=AI_JD_FOR_RESUME).json()["job"]["id"]
    application_id = client.get(f"/jobs/{job_id}").json()["application"]["id"]

    body = client.post("/autofill/resolve", json={
        "fields": [], "application_id": application_id, "shape": "ai_engineer",
    }).json()

    assert body["resume_url"] == "/resume/ai_engineer.pdf"


def test_the_tailored_pdf_is_actually_served(client):
    job_id = post_job(client, description=AI_JD_FOR_RESUME).json()["job"]["id"]
    client.post(f"/jobs/{job_id}/tailor")
    application_id = client.get(f"/jobs/{job_id}").json()["application"]["id"]

    response = client.get(f"/applications/{application_id}/resume.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_asking_for_a_resume_before_tailoring_says_what_to_do(client):
    job_id = post_job(client, description=AI_JD_FOR_RESUME).json()["job"]["id"]
    application_id = client.get(f"/jobs/{job_id}").json()["application"]["id"]

    response = client.get(f"/applications/{application_id}/resume.pdf")
    assert response.status_code == 409
    assert "tailor" in response.json()["detail"]


def test_a_missing_tailored_file_falls_back_rather_than_failing_the_fill(client):
    """The file can be deleted out from under the database — a cleared
    data/resumes, a restored backup. The fill should still attach something."""
    from api.db import Application

    job_id = post_job(client, description=AI_JD_FOR_RESUME).json()["job"]["id"]
    client.post(f"/jobs/{job_id}/tailor")
    application_id = client.get(f"/jobs/{job_id}").json()["application"]["id"]

    session = client.session_factory()
    application = session.get(Application, application_id)
    application.resume_path = "data/resumes/deleted-by-hand.pdf"
    session.commit()
    session.close()

    body = client.post("/autofill/resolve", json={
        "fields": [], "application_id": application_id,
    }).json()
    assert body["resume_url"] == "/resume/ai_engineer.pdf"
