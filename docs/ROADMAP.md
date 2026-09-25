# Roadmap

Ordered so that each phase is independently useful. If you stop after Phase 2 you still have
something that saves you hours a week. Do not build Phase 5 first — discovery without a
working apply-and-track loop just gives you a bigger pile of jobs you have not applied to.

Rough effort assumes evenings and weekends, one person.

---

## Phase 0 — Fact bank and renderer (~3 evenings)

The foundation everything else reads from.

- `profile.yaml` with every bullet from your history, tagged with `skills`, `metrics`,
  `strength`, `aliases`.
- Typst template + `render.py` → ATS-safe PDF.
- DOCX renderer from the same source.
- **Test:** extract text back out of the generated PDF and assert it contains every rendered
  bullet, in order, with contact details present.

**Done when:** `python -m resume.render --profile profile.yaml --out master.pdf` produces a
resume you would actually send, and the extraction test passes.

---

## Phase 1 — Capture and track (~4 evenings)

The minimum that beats a manual spreadsheet.

- FastAPI service: `POST /jobs`, `GET /jobs`, `PATCH /applications/{id}`, SQLite + Alembic.
- MV3 extension with a "Save this job" button and capture adapters for LinkedIn job pages,
  Greenhouse, Lever, Ashby, and a generic fallback that grabs `<title>` + selected text.
- Dedupe on `dedupe_key`.
- Sheets mirror: worker pushes all rows; `Status` and `Notes` sync back.

**Done when:** you can hit one key on any job page and the row appears in your sheet with
company, title, location, apply URL, and source — no duplicates.

---

## Phase 2 — Assisted apply (~1 week)

The phase that actually saves the time.

- Side-panel job queue: filter, sort, open apply URL.
- Autofill adapters for Greenhouse, Lever, Ashby.
- `answers` bank for the recurring dozen questions, editable from the panel.
- Resume attach: drag-and-drop from the panel plus direct file-input set where allowed.
- Highlight every field the system touched; store each value in `events`.
- Confirmation-page detection → auto-transition to `applied`.
- Double-apply warning.

**Done when:** a Greenhouse application goes from panel to submitted in under 30 seconds and
the status flips to `applied` by itself.

---

## Phase 3 — Scoring and tailoring (~1 week)

- JD extraction into a Pydantic schema, cached on `description_hash`.
- Match scoring; threshold gate; skip reasons recorded.
- Constrained tailoring (select → rephrase → validate) per §4 of the architecture.
- The `fact_id` traceability validator, with tests for the failure cases: invented bullet,
  invented number, skill not in profile.
- Keyword gap report per job.
- Write the tailored resume to `resumes/` and a readable copy to a Google Docs folder; link
  both from the sheet.

**Done when:** a tailored resume for a real JD passes validation, reads better than your
master for that role, and the gap report tells you honestly what you are missing.

---

## Phase 4 — Email triage (~4 evenings)

- Gmail read-only OAuth; incremental sync on `history.startHistoryId`.
- Classifier + extractor; assessment deadlines and interview slots pulled out.
- Email → application matcher with the orphan bucket.
- Status write-back to DB and sheet.
- 08:00 digest; instant push for interview / assessment / offer.
- `ghosted` auto-transition at 21 days.

**Done when:** a rejection email moves a row to `rejected` without you touching it, and an
assessment email reaches your phone within the hour.

---

## Phase 5 — Discovery (~1 week)

- Company watchlist with detected `ats_type`.
- Nightly pull from public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters) — these are free, documented, and pleasant to consume.
- Feed-based sources for remote roles.
- Upsert on `dedupe_key`; score everything; queue the top N for tailoring.
- Digest section: "new, scored above threshold, tailored and ready."

**Done when:** you wake up to five apply-ready packets you did not have to find.

---

## Phase 6 — Hardening, once it is in daily use

- Workday / Taleo / iCIMS adapters, partial-fill accepted as a win.
- Naukri / Instahyre hosted-profile sync.
- Field-mapping learning loop from your corrections.
- Response-rate analytics: which resume variants, seniority levels and sources convert. This is
  the part that eventually makes you better at applying, not just faster.
- Postgres migration if the SQLite file ever becomes inconvenient.

---

## Open questions to settle before Phase 1

1. **Geography and boards.** Indian portals (Naukri, Instahyre, Cutshort, Hirist) need
   different adapters from US/EU ATSs, and the hosted-profile model differs from per-job
   forms. Which matters most to you first?
2. **Where the backend runs.** Localhost only (simplest, works when your laptop is on) versus a
   small always-on VPS (cron actually fires nightly, costs a few dollars a month).
3. **Resume format demanded most often** by your target roles — PDF only, or is DOCX a
   frequent requirement?
4. **Python for the backend** as recommended, or TypeScript end-to-end to keep one language
   across extension and server?
