# Roadmap

Ordered so that each phase is independently useful. If you stop after Phase 2 you still have
something that saves you hours a week. Do not build Phase 5 first — discovery without a
working apply-and-track loop just gives you a bigger pile of jobs you have not applied to.

Rough effort assumes evenings and weekends, one person.

---

## Phase 0 — Fact bank and renderer — **done**

The foundation everything else reads from. Built and tested; see the README for usage.

- `profile.yaml` schema (Pydantic) plus a filled-in template: every bullet from your history
  tagged with `shapes`, `skills`, `metrics`, `strength`, `aliases`.
- Typst template + `render.py` → ATS-safe PDF, with the work-authorisation line driven by
  `permit_type`.
- DOCX renderer from the same source.
- Per-shape depth check: warn when a shape has too few strong facts to fill a resume.
- **Test:** extract text back out of the generated PDF and assert it contains every rendered
  bullet, in order, with contact details present.

**Done:** `python -m resume.render --profile profile.yaml --shape ml_platform --out out.pdf`
renders PDF and DOCX, the traceability and extraction checks both gate the output, and the depth
check reports per shape. The extraction check earned itself on the first run — it caught
right-aligned employment dates being extracted mid-bullet, which an ATS would have read as
mangled prose.

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

## Phase 2 — Assisted apply, Workday included (~2.5 weeks)

The phase that actually saves the time. Longer than the others because Workday is in it from the
start, which is the right call for this market — see the cost note below.

- Side-panel job queue: filter, sort, open apply URL.
- Shared field-map layer: `label-regex → profile key`, per-adapter overrides, click-then-pick
  helpers for non-native dropdowns and typeaheads.
- Autofill adapters for Greenhouse, Lever, Ashby — built first *within* this phase, because they
  are two or three days total and they prove the field-map layer before Workday leans on it.
- **Workday adapter** (~1.5 weeks on its own):
  - `ats_accounts` + OS-keychain vault, one account per tenant.
  - Resume-upload-then-correct flow: diff Workday's own parse against `profile.yaml` and fix the
    deltas, rather than filling blanks.
  - Resumable multi-step wizard with per-application step progress.
  - "Use my last application" shortcut when `last_application_id` is set for that company.
- `self_id` filled from canonical values with a **per-adapter category map** — Canadian
  employment-equity wording versus US EEO-1 wording are not interchangeable. Mark and skip
  wherever no confident mapping exists.
- `answers` bank for the recurring dozen questions, editable from the panel.
- Resume attach: drag-and-drop from the panel plus direct file-input set where allowed.
- Highlight every field the system touched; store each value in `events`.
- Confirmation-page detection → auto-transition to `applied`.
- Double-apply warning.

**Done when:** a Greenhouse application goes from panel to submitted in under 30 seconds and
the status flips to `applied` by itself.

**Cost of Workday-first, stated plainly:** the Workday adapter is roughly a week and a half
against two or three days for the other three combined, so it pushes this phase from about one
week to about two and a half. It is worth it here — the banks, their AI labs, the telecoms and
the insurers are where much of Canada's senior Python and AI hiring sits, and the per-tenant
account friction it removes is the worst part of applying there. Build the easy three first inside
the phase so you are applying for real within days rather than waiting out the Workday work.
Partial fill (steps 1–2, then hand over) counts as done for the first pass.

---

## Phase 3 — Scoring and tailoring (~1 week)

- JD extraction into a Pydantic schema, cached on `description_hash`.
- Match scoring; threshold gate; skip reasons recorded.
- Work-authorisation handling: no sponsorship needed, so no discovery filter — but the
  sponsorship answer is *derived* from `permit_type`, and permit expiry raises a flag on distant
  start dates and on long-cycle employers.
- Multi-city requisition collapse: one job, a set of locations, one tailored resume.
- JD **shape classifier**: assigns one primary shape per posting, so each tailored resume is
  single-shaped even though the fact bank covers all three.
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

- Workday adapter hardening: full-flow instead of partial fill, selector re-verification after
  Workday releases.
- Taleo — same account-per-employer problem, messier selectors.
- iCIMS, BambooHR, Dayforce — the long tail, once you meet the same one twice.
- GC Jobs profile sync, only if federal roles are in scope.
- Field-mapping learning loop from your corrections.
- Response-rate analytics: which resume variants, seniority levels and sources convert. This is
  the part that eventually makes you better at applying, not just faster.
- Postgres migration if the SQLite file ever becomes inconvenient.

---

## Decisions settled

- **Market:** Canadian AI, tech and Python roles, country-wide.
- **Backend:** Python + FastAPI, bound to `127.0.0.1`.
- **Hosting:** localhost through Phase 3. Move to a small always-on VPS at Phase 4, when
  scheduled Gmail triage and nightly discovery need to run whether or not the laptop is awake.
  Add API auth as part of that move, not after it.
- **Workday from day one**, alongside Greenhouse/Lever/Ashby in Phase 2. Accepted cost:
  Phase 2 runs ~2.5 weeks instead of ~1.
- **Work authorisation:** PGWP, an open permit with more than two years left. No sponsorship
  required and no expiry flagging for now, though the flagging logic ships dormant since a PGWP is
  single-use and non-renewable. The resume carries an authorisation line.
- **Location:** Canada-wide, willing to relocate, remote or hybrid both fine. Location is out of
  `dedupe_key`; multi-city reqs collapse to one job with a set of locations.
- **Self-identification:** disclose where asked — man, not Indigenous, racialized (South Asian),
  no disability, not a veteran. Stored canonically and mapped per ATS; never inferred from
  anything else in the profile.
- **Role shapes:** `ai_engineer`, `backend_python`, `fullstack` — revised from the generic
  taxonomy to match the real history. All three in the fact bank, **one shape per application**,
  chosen by a JD classifier. Breadth in the bank, focus on every rendered resume.
- **Resume format:** reproduces the existing resume rather than replacing it. Right-aligned dates
  stay the default, with `--date-style inline` available for maximum parser safety.
- **Compensation and notice:** flexible; no salary filter on discovery, and the answer bank says
  so rather than naming a number that could anchor low.
- **French:** assumed `none` until told otherwise — French-required Quebec and federally
  regulated postings are scored down, while Montreal's English-language AI roles stay in scope.

## Still open

Nothing blocking Phase 1.

1. **Workday tenant URLs** for the ten enterprises in `discover/watchlist.yaml`. A tenant
   (`<employer>.wdN.myworkdayjobs.com/<site>`) cannot be derived from a company name, so it is
   the one part of the watchlist that has to be pasted in by hand — once per employer, ever.
2. **Run `python -m discover.detect`** somewhere with network access to the job-board hosts, and
   commit the resulting `discover/detected.yaml`.
3. **Whether `profile.yaml` should be committed.** It is gitignored today because it holds
   personal data; committing it to a private repository would make the fact bank survive a fresh
   checkout.
4. **Fact bank depth for `ai_engineer`** — four bullets support the shape you most want to be
   hired for. Another shipped AI project is the fix the depth check is asking for.
