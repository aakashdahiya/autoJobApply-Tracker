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
