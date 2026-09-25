"""The nightly sweep: read the watchlist's boards, keep what fits, score it.

Runs after `discover.detect` has worked out which ATS each company uses. The
ordering matters — discovery without the apply-and-track loop in front of it
just produces a bigger pile of jobs you have not applied to, which is why this
is the last phase rather than the first.

Politeness is the same as detect: public endpoints, one request per second,
an honest User-Agent, no logins.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from api.db import Status, make_engine, make_session_factory
from api.store import capture_job
from discover.boards import BOARDS, fetch_board
from discover.detect import DELAY_SECONDS, fetch_json
from discover.filters import keep


@dataclass
class CrawlReport:
    companies: int = 0
    boards_read: int = 0
    board_errors: list[str] = field(default_factory=list)
    postings_seen: int = 0
    filtered_out: int = 0
    new_jobs: int = 0
    already_known: int = 0
    passed_gate: int = 0
    skipped_by_gate: int = 0
    tailored: list[str] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.boards_read}/{self.companies} boards read, "
            f"{self.postings_seen} postings, {self.filtered_out} filtered out, "
            f"{self.new_jobs} new ({self.already_known} already known), "
            f"{self.passed_gate} passed the gate, {self.skipped_by_gate} skipped"
        )


def load_companies(path: str | Path) -> list[dict]:
    """Prefer `detected.yaml`; fall back to the watchlist with no ATS resolved."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return [c for c in data.get("companies", []) if c.get("ats") and c.get("slug")]


def crawl(
    session,
    companies: list[dict],
    *,
    fetch=fetch_json,
    delay: float = DELAY_SECONDS,
    per_board: int | None = None,
    tailor_top: int = 0,
    use_model: bool = False,
    profile_path: str = "profile.yaml",
) -> CrawlReport:
    from resume.loader import load_profile
    from tailor.pipeline import analyse_and_score, record_score, tailor as run_tailor

    profile = load_profile(profile_path)
    report = CrawlReport(companies=len(companies))
    candidates: list[tuple[float, int, object]] = []

    for entry in companies:
        ats, slug = entry["ats"], entry["slug"]
        if ats not in BOARDS:
            report.board_errors.append(f"{entry['name']}: no reader for {ats}")
            continue

        result = fetch_board(entry["name"], ats, slug, fetch=fetch)
        if delay:
            time.sleep(delay)
        if result.error:
            report.board_errors.append(f"{entry['name']}: {result.error}")
            continue
        report.boards_read += 1

        postings = result.postings[:per_board] if per_board else result.postings
        for posting in postings:
            report.postings_seen += 1
            wanted, _why = keep(posting.title, posting.location)
            if not wanted:
                report.filtered_out += 1
                continue

            job, application, created = capture_job(session, {
                "title": posting.title,
                "company": entry["name"],
                "apply_url": posting.apply_url,
                "location": posting.location,
                "description": posting.description,
                "source": ats,
                "ats_type": ats,
            })
            if not created:
                report.already_known += 1
                continue
            report.new_jobs += 1

            if not (job.description_raw or "").strip():
                continue  # nothing to score against; it waits for a capture
            _jd, score = analyse_and_score(profile, job)
            record_score(session, job, score)
            if score.passes:
                report.passed_gate += 1
                candidates.append((score.total, len(score.required_matched), job))
            else:
                report.skipped_by_gate += 1

    # Tailor the best of tonight's haul, not the first ones seen. Ties on the
    # ratio break toward more matched requirements: a posting naming two things
    # you have is a weaker signal than one naming seven, even though both come
    # out at 100% coverage.
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    for _total, _matched, job in candidates[:tailor_top] if tailor_top else []:
        jd, score = analyse_and_score(profile, job)
        outcome = run_tailor(session, profile, job, jd, score, use_model=use_model)
        label = f"{job.company.name} — {job.title} ({score.total:.0f})"
        report.tailored.append(label if outcome.tailored else f"{label}: {outcome.note}")

    report.ready = [
        f"{job.company.name} — {job.title} ({total:.0f})"
        for total, _matched, job in candidates[:10]
    ]
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="discover.crawl", description=__doc__)
    ap.add_argument("--detected", default="discover/detected.yaml",
                    help="output of `python -m discover.detect`")
    ap.add_argument("--profile", default="profile.yaml")
    ap.add_argument("--only", help="substring filter on company name")
    ap.add_argument("--per-board", type=int, help="cap postings read per company")
    ap.add_argument("--tailor-top", type=int, default=0,
                    help="render resumes for the N best-scoring new jobs")
    ap.add_argument("--use-model", action="store_true", help="rephrase while tailoring")
    ap.add_argument("--delay", type=float, default=DELAY_SECONDS)
    args = ap.parse_args(argv)

    path = Path(args.detected)
    if not path.exists():
        print(f"{path} not found — run `python -m discover.detect` first.")
        return 1

    companies = load_companies(path)
    if args.only:
        needle = args.only.casefold()
        companies = [c for c in companies if needle in c["name"].casefold()]
    if not companies:
        print("No companies with a resolved ATS. Run detect, or widen --only.")
        return 1

    session = make_session_factory(make_engine())()
    try:
        report = crawl(
            session, companies, delay=args.delay, per_board=args.per_board,
            tailor_top=args.tailor_top, use_model=args.use_model,
            profile_path=args.profile,
        )
    finally:
        session.close()

    print(report.summary())
    for error in report.board_errors:
        print(f"  ! {error}")
    if report.ready:
        print("\nBest of tonight:")
        for line in report.ready:
            print(f"  • {line}")
    for line in report.tailored:
        print(f"  tailored: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
