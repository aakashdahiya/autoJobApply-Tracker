"""Cache JD analysis by description hash.

The same posting is captured from LinkedIn, the company board and the nightly
crawl. Parsing it three times is free but not instant; re-running the model
over it would not be free at all, which is why the hash key lives here rather
than beside the model call.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from resume.schema import Shape
from tailor.jd import JobDescription, analyse

CACHE_DIR = Path("data/jd_cache")


def _path(description_hash: str) -> Path:
    return CACHE_DIR / f"{description_hash}.json"


def load(description_hash: str) -> JobDescription | None:
    path = _path(description_hash)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None  # a corrupt cache entry is a reason to re-parse, not to fail
    return JobDescription(
        required=set(raw["required"]),
        preferred=set(raw["preferred"]),
        all_terms=set(raw["all_terms"]),
        years_required=raw["years_required"],
        shape=Shape(raw["shape"]),
        shape_scores=raw["shape_scores"],
        shape_confidence=raw["shape_confidence"],
    )


def save(description_hash: str, jd: JobDescription) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = asdict(jd)
    payload["required"] = sorted(jd.required)
    payload["preferred"] = sorted(jd.preferred)
    payload["all_terms"] = sorted(jd.all_terms)
    payload["shape"] = jd.shape.value
    _path(description_hash).write_text(json.dumps(payload, indent=2))


def analyse_cached(text: str, description_hash: str | None) -> JobDescription:
    if not description_hash:
        return analyse(text)
    cached = load(description_hash)
    if cached is not None:
        return cached
    jd = analyse(text)
    save(description_hash, jd)
    return jd
