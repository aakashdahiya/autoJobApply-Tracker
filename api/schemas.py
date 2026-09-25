"""Request and response shapes for the local API."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from api.db import Status


class JobCapture(BaseModel):
    """What the extension sends when you save a posting."""

    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    apply_url: str = Field(min_length=1, max_length=1000)
    location: str | None = None
    locations: list[str] = Field(default_factory=list)
    description: str | None = None
    salary: str | None = None
    source: str = "extension"
    ats_type: str | None = None
    canonical_url: str | None = None
    careers_url: str | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: Status
    match_score: float | None = None
    shape: str | None = None
    resume_path: str | None = None
    applied_at: dt.datetime | None = None
    last_status_change_at: dt.datetime
    next_action: str | None = None
    notes: str | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    company: str
    locations: list[str]
    remote_type: str | None = None
    apply_url: str
    source: str
    salary_raw: str | None = None
    discovered_at: dt.datetime
    is_open: bool
    application: ApplicationOut


class CaptureResult(BaseModel):
    created: bool
    job: JobOut
    duplicate_warning: str | None = None


class ApplicationPatch(BaseModel):
    status: Status | None = None
    notes: str | None = None
    next_action: str | None = None
    match_score: float | None = None
    shape: str | None = None
    resume_path: str | None = None


class Stats(BaseModel):
    total_jobs: int
    by_status: dict[str, int]
    applied_this_week: int
