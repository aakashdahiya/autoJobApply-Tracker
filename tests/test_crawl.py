"""The nightly sweep: reading boards, filtering, and what reaches the queue.

Every test runs offline against recorded board shapes.
"""

from __future__ import annotations

import pytest

from api.db import Job, Status, make_engine, make_session_factory
from discover.boards import BOARDS, Posting, fetch_board, parse, strip_html
from discover.crawl import crawl, load_companies
from discover.filters import keep, location_verdict, relevant_title

JD = "Build RAG pipelines in Python with FastAPI, embeddings and Postgres."


@pytest.fixture
def session():
    s = make_session_factory(make_engine("sqlite://"))()
    yield s
    s.close()


def greenhouse(*titles_locations):
    return {"jobs": [
        {"id": i, "title": t, "location": {"name": loc},
         "absolute_url": f"https://boards.greenhouse.io/demo/jobs/{i}",
         "content": f"&lt;p&gt;{JD}&lt;/p&gt;", "updated_at": "2026-09-20"}
        for i, (t, loc) in enumerate(titles_locations, start=1)
    ]}


def fetcher(mapping):
    return lambda url: mapping.get(url)


# --- board parsing ----------------------------------------------------------

def test_each_board_shape_parses(session):
    shapes = {
        "greenhouse": greenhouse(("Senior AI Engineer", "Toronto, ON")),
        "lever": [{"text": "Backend Engineer", "categories": {"location": "Vancouver, BC"},
                   "hostedUrl": "https://jobs.lever.co/demo/1", "descriptionPlain": JD}],
        "ashby": {"jobs": [{"title": "ML Engineer", "location": "Remote - Canada",
                            "jobUrl": "https://jobs.ashbyhq.com/demo/1",
                            "descriptionPlain": JD}]},
        "workable": {"jobs": [{"title": "Python Developer", "city": "Ottawa",
                               "url": "https://apply.workable.com/demo/j/1",
                               "description": JD}]},
        "recruitee": {"offers": [{"title": "Full Stack Developer", "location": "Calgary, AB",
                                  "careers_url": "https://demo.recruitee.com/o/1",
                                  "description": JD}]},
    }
    for ats, payload in shapes.items():
        postings = parse(BOARDS[ats], payload, "Demo")
        assert len(postings) == 1, ats
        assert postings[0].usable, ats
        assert postings[0].title and postings[0].apply_url, ats


def test_a_renamed_field_falls_through_to_the_next_candidate():
    """The reason fields are lists: a vendor rename must not be a crash."""
    payload = {"jobs": [{"name": "Senior AI Engineer", "location": "Toronto, ON",
                         "url": "https://x/1", "description": JD}]}
    posting = parse(BOARDS["greenhouse"], payload, "Demo")[0]
    assert posting.title == "Senior AI Engineer"
    assert posting.apply_url == "https://x/1"


def test_a_posting_missing_its_url_is_dropped_not_half_saved():
    payload = {"jobs": [{"title": "Engineer"}, {"title": "Dev", "absolute_url": "https://x/2"}]}
    result = fetch_board("Demo", "greenhouse", "demo", fetch=lambda _u: payload)
    assert len(result.postings) == 1 and result.dropped == 1


def test_html_descriptions_are_unescaped_and_flattened():
    assert strip_html("&lt;p&gt;One&lt;br&gt;Two&lt;/p&gt;") == "One\nTwo"
    assert strip_html("") == ""


def test_a_board_that_does_not_answer_is_reported_not_raised():
    result = fetch_board("Demo", "greenhouse", "demo", fetch=lambda _u: None)
    assert result.error and not result.postings


def test_an_unknown_ats_is_reported():
    assert "no reader" in fetch_board("Demo", "bamboohr", "demo", fetch=lambda _u: {}).error


# --- filtering --------------------------------------------------------------

def test_only_the_three_shapes_get_through():
    assert relevant_title("Senior AI Engineer")
    assert relevant_title("Full Stack Developer")
    assert not relevant_title("Account Executive")
    assert not relevant_title("Engineering Manager")
    assert not relevant_title("Hardware Engineer")


def test_an_ambiguous_city_needs_a_qualifier():
    """London is a real Ontario city and a much more famous English one."""
    assert location_verdict("London, ON") == "canada"
    assert location_verdict("London, UK") == "elsewhere"
    assert location_verdict("Sydney, NS") == "canada"
    assert location_verdict("Sydney, Australia") == "elsewhere"


def test_a_blank_location_is_treated_as_remote_not_discarded():
    assert location_verdict("") == "remote"
    assert keep("Backend Engineer", "")[0]


# --- the sweep --------------------------------------------------------------

def companies(ats="greenhouse", slug="demo", name="Demo"):
    return [{"name": name, "ats": ats, "slug": slug}]


def test_the_sweep_keeps_canadian_engineering_roles_and_drops_the_rest(session):
    payload = greenhouse(
        ("Senior AI Engineer", "Toronto, ON"),
        ("Software Engineer", "San Francisco, CA"),
        ("Account Executive", "Toronto, ON"),
        ("Backend Engineer", "Remote - Canada"),
    )
    url = BOARDS["greenhouse"].url.format(slug="demo")
    report = crawl(session, companies(), fetch=fetcher({url: payload}), delay=0)

    assert report.postings_seen == 4
    assert report.filtered_out == 2
    assert report.new_jobs == 2
    titles = {job.title for job in session.query(Job).all()}
    assert titles == {"Senior AI Engineer", "Backend Engineer"}


def test_rerunning_the_sweep_adds_nothing(session):
    url = BOARDS["greenhouse"].url.format(slug="demo")
    payload = greenhouse(("Senior AI Engineer", "Toronto, ON"))
    fetch = fetcher({url: payload})

    crawl(session, companies(), fetch=fetch, delay=0)
    second = crawl(session, companies(), fetch=fetch, delay=0)

    assert second.new_jobs == 0 and second.already_known == 1
    assert session.query(Job).count() == 1


def test_everything_new_is_scored_and_the_gate_records_a_skip(session):
    payload = {"jobs": [
        {"id": 1, "title": "Senior AI Engineer", "location": {"name": "Toronto, ON"},
         "absolute_url": "https://x/1", "content": JD},
        {"id": 2, "title": "Backend Engineer", "location": {"name": "Toronto, ON"},
         "absolute_url": "https://x/2",
         "content": "10+ years of Java, Scala, Kubernetes, Terraform and AWS."},
    ]}
    url = BOARDS["greenhouse"].url.format(slug="demo")
    report = crawl(session, companies(), fetch=fetcher({url: payload}), delay=0)

    assert report.passed_gate == 1 and report.skipped_by_gate == 1
    statuses = {j.title: j.application.status for j in session.query(Job).all()}
    assert statuses["Senior AI Engineer"] is Status.scored
    assert statuses["Backend Engineer"] is Status.skipped


def test_ties_break_toward_the_posting_with_more_evidence(session):
    """Two jobs at 100% coverage are not equally good bets: one named two
    things you have, the other named seven."""
    payload = {"jobs": [
        {"id": 1, "title": "Backend Engineer", "location": {"name": "Toronto, ON"},
         "absolute_url": "https://x/1", "content": "Python and Postgres."},
        {"id": 2, "title": "Senior AI Engineer", "location": {"name": "Toronto, ON"},
         "absolute_url": "https://x/2",
         "content": "Python, FastAPI, RAG, LLMs, embeddings, semantic search, Postgres."},
    ]}
    url = BOARDS["greenhouse"].url.format(slug="demo")
    report = crawl(session, companies(), fetch=fetcher({url: payload}),
                   delay=0, tailor_top=1)

    assert len(report.tailored) == 1
    assert "Senior AI Engineer" in report.tailored[0]


def test_a_failing_board_does_not_stop_the_others(session):
    good = BOARDS["greenhouse"].url.format(slug="good")
    report = crawl(
        session,
        [{"name": "Bad", "ats": "greenhouse", "slug": "bad"},
         {"name": "Good", "ats": "greenhouse", "slug": "good"}],
        fetch=fetcher({good: greenhouse(("Senior AI Engineer", "Toronto, ON"))}),
        delay=0,
    )
    assert report.boards_read == 1 and len(report.board_errors) == 1
    assert report.new_jobs == 1


def test_a_posting_with_no_description_is_kept_but_not_scored(session):
    payload = {"jobs": [{"id": 1, "title": "Senior AI Engineer",
                         "location": {"name": "Toronto, ON"}, "absolute_url": "https://x/1"}]}
    url = BOARDS["greenhouse"].url.format(slug="demo")
    report = crawl(session, companies(), fetch=fetcher({url: payload}), delay=0)

    assert report.new_jobs == 1
    assert report.passed_gate == 0 and report.skipped_by_gate == 0
    assert session.query(Job).one().application.status is Status.discovered


def test_load_companies_ignores_entries_without_a_resolved_ats(tmp_path):
    path = tmp_path / "detected.yaml"
    path.write_text(
        "companies:\n"
        "  - {name: A, ats: greenhouse, slug: a}\n"
        "  - {name: RBC, ats: null, slug: null, workday_tenant: null}\n"
    )
    loaded = load_companies(path)
    assert [c["name"] for c in loaded] == ["A"]
