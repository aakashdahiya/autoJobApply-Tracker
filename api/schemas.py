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


class FieldIn(BaseModel):
    """One control the content script found on the form."""

    id: str
    label: str = ""
    type: str = "text"
    options: list[str] = Field(default_factory=list)
    required: bool = False
    automation_id: str = ""


class ResolveRequest(BaseModel):
    fields: list[FieldIn]
    job_id: int | None = None
    ats: str | None = None
    shape: str | None = None


class FillOut(BaseModel):
    field_id: str
    value: str | None = None
    action: str
    source: str = ""
    confidence: str = "low"
    reason: str = ""


class ResolveResponse(BaseModel):
    fills: list[FillOut]
    filled: int
    skipped: int
    unmatched: int
    resume_url: str | None = None


class AccountIn(BaseModel):
    ats_type: str = "workday"
    tenant: str
    username: str
    password: str
    company: str | None = None
    notes: str | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ats_type: str
    tenant: str
    username: str
    last_application_id: int | None = None
    notes: str | None = None


class AutofillLog(BaseModel):
    application_id: int
    ats: str
    step: str | None = None
    filled: dict[str, str | None] = Field(default_factory=dict)
    corrected: dict[str, str | None] = Field(default_factory=dict)
