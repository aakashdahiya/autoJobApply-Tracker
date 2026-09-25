"""SQLite schema. Source of truth for everything the system knows."""

from __future__ import annotations

import datetime as dt
import enum
import os
from pathlib import Path

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    """Application lifecycle. `ghosted` is reached automatically after silence,
    so the pipeline reflects reality rather than 200 rows of false hope."""

    discovered = "discovered"
    scored = "scored"
    tailored = "tailored"
    ready = "ready"
    applied = "applied"
    acknowledged = "acknowledged"
    screening = "screening"
    assessment = "assessment"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    ghosted = "ghosted"
    skipped = "skipped"


OPEN_STATUSES = {
    Status.discovered, Status.scored, Status.tailored, Status.ready,
    Status.applied, Status.acknowledged, Status.screening,
    Status.assessment, Status.interview,
}


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_norm: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(200))
    ats_type: Mapped[str | None] = mapped_column(String(40))
    careers_url: Mapped[str | None] = mapped_column(String(500))
    watchlist: Mapped[bool] = mapped_column(Boolean, default=False)
    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    title_norm: Mapped[str] = mapped_column(String(300), index=True)
    # A set, not a string: enterprises post one requisition across several cities.
    locations: Mapped[list] = mapped_column(JSON, default=list)
    remote_type: Mapped[str | None] = mapped_column(String(20))
    seniority: Mapped[str | None] = mapped_column(String(40))
    description_raw: Mapped[str | None] = mapped_column(Text)
    description_hash: Mapped[str | None] = mapped_column(String(40), index=True)
    apply_url: Mapped[str] = mapped_column(String(1000))
    canonical_url: Mapped[str | None] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(40))
    salary_raw: Mapped[str | None] = mapped_column(String(200))
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    dedupe_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="jobs")
    application: Mapped["Application"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True, index=True)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.discovered, index=True)
    match_score: Mapped[float | None] = mapped_column(Float)
    shape: Mapped[str | None] = mapped_column(String(40))
    resume_path: Mapped[str | None] = mapped_column(String(500))
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    last_status_change_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    next_action: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="application")
    events: Mapped[list["Event"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class EmailLink(Base):
    """One Gmail message, and what we made of it.

    Keyed on the Gmail message id so a re-sync is idempotent. `application_id`
    is null for an orphan — an email we could not confidently attach to an
    application, which goes in the digest for you to place rather than being
    guessed at.
    """

    __tablename__ = "email_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(80), index=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id"), index=True
    )
    classification: Mapped[str] = mapped_column(String(40), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    sender: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    deadline_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    extracted: Mapped[dict | None] = mapped_column(JSON)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class SyncState(Base):
    """Where the last Gmail sync got to.

    Incremental via historyId: a sweep reads the handful of messages that
    arrived, never the whole mailbox.
    """

    __tablename__ = "sync_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), unique=True)
    cursor: Mapped[str | None] = mapped_column(String(80))
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class AtsAccount(Base):
    """One row per Workday/Taleo tenant, because each employer is its own login.

    `secret_ref` points into the OS keychain. There is deliberately no password
    column: a database file is copied, backed up and opened far more casually
    than a keychain is.
    """

    __tablename__ = "ats_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    ats_type: Mapped[str] = mapped_column(String(40))
    tenant: Mapped[str] = mapped_column(String(200), index=True)
    username: Mapped[str] = mapped_column(String(200))
    secret_ref: Mapped[str] = mapped_column(String(300), unique=True)
    last_application_id: Mapped[int | None] = mapped_column(Integer)
    profile_last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    """Append-only. When you wonder why the system thinks something, look here."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    payload: Mapped[dict | None] = mapped_column(JSON)

    application: Mapped[Application] = relationship(back_populates="events")


DEFAULT_DB = Path("data/tracker.sqlite")


def make_engine(url: str | None = None):
    """Build an engine and ensure the schema exists.

    In-memory SQLite needs StaticPool: without it every connection gets its own
    fresh, empty database, so the tables created here vanish before the next
    query. That bites tests only, but it bites them confusingly.
    """
    if url is None:
        url = os.getenv("TRACKER_DB_URL") or ""
    if not url:
        url = f"sqlite:///{DEFAULT_DB}"

    # Create the directory for any SQLite file, not just the default one.
    # Otherwise pointing TRACKER_DB_URL at a path whose folder does not exist
    # yet fails with a bare "unable to open database file".
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url[len("sqlite:///") :]).expanduser().parent.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in url or url == "sqlite://":
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
