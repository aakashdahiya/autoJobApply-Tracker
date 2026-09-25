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
  Indeed Canada, Greenhouse, Lever, Ashby, and a generic fallback that grabs `<title>` +
  selected text.
- Dedupe on `dedupe_key`.
- Sheets mirror: worker pushes all rows; `Status` and `Notes` sync back.

**Done when:** you can hit one key on any job page and the row appears in your sheet with
company, title, location, apply URL, and source — no duplicates.

---

## Phase 2 — Assisted apply (~1 week)

The phase that actually saves the time.

- Side-panel job queue: filter, sort, open apply URL.
- Autofill adapters for Greenhouse, Lever, Ashby.
- Leave voluntary self-identification and accommodation fields untouched; mark and skip them.
- `answers` bank for the recurring dozen questions, editable from the panel.
- Resume attach: drag-and-drop from the panel plus direct file-input set where allowed.
- Highlight every field the system touched; store each value in `events`.
- Confirmation-page detection → auto-transition to `applied`.
- Double-apply warning.

**Done when:** a Greenhouse application goes from panel to submitted in under 30 seconds and
the status flips to `applied` by itself.

**Canada note:** if your target list leans toward banks, telecoms and insurers, a Workday adapter
earns its keep immediately rather than in Phase 6 — that is where much of the senior Python and
AI hiring sits. Partial fill (steps 1–2, then hand over) is a real win there.

---

## Phase 3 — Scoring and tailoring (~1 week)

- JD extraction into a Pydantic schema, cached on `description_hash`.
- Match scoring; threshold gate; skip reasons recorded.
- Work-authorisation filter: postings that cannot sponsor are scored down or skipped when
  `work_auth` calls for it, and permit expiry raises a flag against distant start dates.
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

- Canadian company watchlist with detected `ats_type`, seeded from the Toronto, Waterloo,
  Montreal and Vancouver tech and AI ecosystems.
- Nightly pull from public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters) — these are free, documented, and pleasant to consume.
- Job Bank (jobbank.gc.ca) postings feed — confirm the current feed format before building
  against it. High volume, mixed tech signal, so it leans hard on the score gate.
- Feed-based sources for Canada-remote roles.
- Upsert on `dedupe_key`; score everything; queue the top N for tailoring.
- Digest section: "new, scored above threshold, tailored and ready."

**Done when:** you wake up to five apply-ready packets you did not have to find.

---

## Phase 6 — Hardening, once it is in daily use

- Workday / Taleo adapters if not already pulled forward; partial-fill accepted as a win.
- iCIMS, BambooHR, Dayforce — the long tail, once you meet the same one twice.
- GC Jobs profile sync, only if federal roles are in scope.
- Field-mapping learning loop from your corrections.
- Response-rate analytics: which resume variants, seniority levels and sources convert. This is
  the part that eventually makes you better at applying, not just faster.
- Postgres migration if the SQLite file ever becomes inconvenient.

---

## Decisions settled

- **Market:** Canadian AI, tech and Python roles. Adapter and discovery priorities follow
  §9 of the architecture; Workday matters more here than in a startup-only market.
- **Backend:** Python + FastAPI, bound to `127.0.0.1`.
- **Hosting:** localhost through Phase 3. Move to a small always-on VPS at Phase 4, when
  scheduled Gmail triage and nightly discovery need to run whether or not the laptop is awake.
  Add API auth as part of that move, not after it.

## Still open

1. **Work authorisation status** — citizen, PR, PGWP, or needs sponsorship. This is not a
   detail: it drives the discovery filter, the scoring adjustment, and whether the resume
   should state status near the top.
2. **Province and remote appetite** — which provinces count as local, and whether
   Canada-remote-only postings are in or out.
3. **Role shape weighting** — research-adjacent ML, ML/platform engineering, or product
   Python. Most people want two of the three; the third is a distraction worth filtering out.
4. **French proficiency**, which decides whether Quebec and federally-regulated postings are
   worth scoring at all.
