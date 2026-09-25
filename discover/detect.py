"""Work out which ATS each watchlist company uses, by asking their board.

Guessing an ATS from memory goes stale; probing does not. A hit means the
company publishes a job board at that token, which is exactly the thing Phase 5
needs to poll nightly.

Politeness is not optional here: one request per host per second, a real
timeout, an honest User-Agent, and no retries on a clean 404.

    python -m discover.detect --watchlist discover/watchlist.yaml \
        --out discover/detected.yaml
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from discover.probes import PROBES, Probe

USER_AGENT = "autoJobApply-Tracker/0.1 (personal job search; contact via repo)"
TIMEOUT = 15
DELAY_SECONDS = 1.0


@dataclass
class Detection:
    name: str
    city: str | None = None
    ats: str | None = None
    slug: str | None = None
    board_url: str | None = None
    open_jobs: int | None = None
    workday_tenant: str | None = None
    notes: list[str] = field(default_factory=list)


def fetch_json(url: str) -> object | None:
    """GET a JSON endpoint. None on any non-200 or unparseable body."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def probe_company(
    company: dict, *, fetch=fetch_json, delay: float = DELAY_SECONDS
) -> Detection:
    """Try each candidate slug against each ATS; first hit with jobs wins."""
    found = Detection(
        name=company["name"],
        city=company.get("city"),
        workday_tenant=company.get("workday_tenant"),
    )
    slugs = company.get("slugs") or []
    if not slugs:
        found.notes.append("no candidate slugs; fill in workday_tenant by hand")
        return found

    best: tuple[Probe, str, int] | None = None
    for slug in slugs:
        for probe in PROBES:
            url = probe.url.format(slug=slug)
            count = probe.extract(fetch(url))
            if delay:
                time.sleep(delay)
            if count is None:
                continue
            if best is None or count > best[2]:
                best = (probe, slug, count)
            if count > 0:
                break
        if best and best[2] > 0:
            break

    if best is None:
        found.notes.append("no public board found for any candidate slug")
        return found

    probe, slug, count = best
    found.ats, found.slug = probe.ats, slug
    found.board_url = probe.url.format(slug=slug)
    found.open_jobs = count
    if count == 0:
        found.notes.append("board resolves but lists no open roles right now")
    return found


def detect(watchlist: list[dict], *, fetch=fetch_json, delay: float = DELAY_SECONDS) -> list[Detection]:
    return [probe_company(c, fetch=fetch, delay=delay) for c in watchlist]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="discover.detect", description=__doc__)
    ap.add_argument("--watchlist", default="discover/watchlist.yaml")
    ap.add_argument("--out", default="discover/detected.yaml")
    ap.add_argument("--only", help="substring filter on company name")
    ap.add_argument("--delay", type=float, default=DELAY_SECONDS)
    args = ap.parse_args(argv)

    companies = yaml.safe_load(Path(args.watchlist).read_text())["companies"]
    if args.only:
        needle = args.only.casefold()
        companies = [c for c in companies if needle in c["name"].casefold()]

    results = detect(companies, delay=args.delay)

    hits = [r for r in results if r.ats]
    by_ats: dict[str, int] = {}
    for r in hits:
        by_ats[r.ats] = by_ats.get(r.ats, 0) + 1

    for r in results:
        if r.ats:
            print(f"  {r.name:<22} {r.ats:<16} {r.slug:<18} {r.open_jobs or 0} open")
        else:
            print(f"  {r.name:<22} {'-':<16} {'':<18} {'; '.join(r.notes)}")

    Path(args.out).write_text(
        yaml.safe_dump(
            {"companies": [vars(r) for r in results]}, sort_keys=False, allow_unicode=True
        )
    )
    print(f"\n{len(hits)}/{len(results)} resolved: {by_ats}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
