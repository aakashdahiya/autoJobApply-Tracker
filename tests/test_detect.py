"""ATS detection, offline. No test here touches the network."""

from __future__ import annotations

import yaml
from pathlib import Path

from discover.detect import detect, probe_company
from discover.probes import PROBES

WATCHLIST = Path(__file__).resolve().parent.parent / "discover" / "watchlist.yaml"


def fake(responses: dict):
    """A fetcher that answers only the URLs it is given, 404s everything else."""
    return lambda url: responses.get(url)


def test_greenhouse_hit_is_detected():
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=false"
    found = probe_company(
        {"name": "Acme", "city": "Toronto", "slugs": ["acme"]},
        fetch=fake({url: {"jobs": [{"id": 1}, {"id": 2}]}}),
        delay=0,
    )
    assert (found.ats, found.slug, found.open_jobs) == ("greenhouse", "acme", 2)
    assert found.board_url == url


def test_second_candidate_slug_is_tried():
    url = "https://api.lever.co/v0/postings/acme-inc?mode=json"
    found = probe_company(
        {"name": "Acme", "slugs": ["acme", "acme-inc"]},
        fetch=fake({url: [{"id": "a"}]}),
        delay=0,
    )
    assert (found.ats, found.slug) == ("lever", "acme-inc")


def test_board_with_zero_openings_still_resolves_but_is_noted():
    """Knowing a company is on Greenhouse is useful even with nothing open today."""
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    found = probe_company(
        {"name": "Acme", "slugs": ["acme"]}, fetch=fake({url: {"jobs": []}}), delay=0
    )
    assert found.ats == "ashby" and found.open_jobs == 0
    assert any("no open roles" in n for n in found.notes)


def test_a_board_with_openings_beats_an_empty_one():
    found = probe_company(
        {"name": "Acme", "slugs": ["acme"]},
        fetch=fake(
            {
                "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=false": {"jobs": []},
                "https://api.lever.co/v0/postings/acme?mode=json": [{"id": "a"}],
            }
        ),
        delay=0,
    )
    assert found.ats == "lever" and found.open_jobs == 1


def test_no_board_found_is_reported_not_guessed():
    found = probe_company({"name": "Acme", "slugs": ["acme"]}, fetch=fake({}), delay=0)
    assert found.ats is None and found.slug is None
    assert any("no public board" in n for n in found.notes)


def test_malformed_payload_is_not_a_hit():
    """A 200 that is not the expected shape must not be read as a board."""
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=false"
    found = probe_company(
        {"name": "Acme", "slugs": ["acme"]},
        fetch=fake({url: {"error": "not found"}}),
        delay=0,
    )
    assert found.ats is None


def test_workday_company_is_left_for_manual_tenant_entry():
    """A tenant URL cannot be derived from a company name, so it is not guessed."""
    found = probe_company({"name": "RBC", "workday_tenant": None}, fetch=fake({}), delay=0)
    assert found.ats is None
    assert any("workday_tenant" in n for n in found.notes)


def test_watchlist_file_is_well_formed():
    companies = yaml.safe_load(WATCHLIST.read_text())["companies"]
    assert len(companies) >= 40
    names = [c["name"] for c in companies]
    assert len(names) == len(set(names)), "duplicate company"
    for c in companies:
        assert c.get("slugs") or "workday_tenant" in c, c["name"]


def test_detect_runs_the_whole_list():
    companies = [{"name": "A", "slugs": ["a"]}, {"name": "B", "slugs": ["b"]}]
    assert len(detect(companies, fetch=fake({}), delay=0)) == 2


def test_probe_urls_are_all_https_and_unique():
    urls = [p.url for p in PROBES]
    assert len(urls) == len(set(urls))
    assert all(u.startswith("https://") for u in urls)
