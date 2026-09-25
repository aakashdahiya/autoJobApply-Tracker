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

## Phase 1 — Capture and track — **done**

The minimum that beats a manual spreadsheet.

- FastAPI service: `POST /jobs`, `GET /jobs`, `PATCH /applications/{id}`, SQLite + Alembic.
- MV3 extension with a "Save this job" button and capture adapters for LinkedIn job pages,
  Indeed Canada, Greenhouse, Lever, Ashby, and a generic fallback that grabs `<title>` +
  selected text.
- Dedupe on `dedupe_key`.
- Sheets mirror: worker pushes all rows; `Status` and `Notes` sync back.

**Done:** one keystroke on any job page saves the posting with company, title, locations,
apply URL and source, deduped. Verified end to end against a running server.

The Sheets mirror is built too: `python -m sheets.run --spreadsheet <id>`. SQLite remains the
source of truth and only Status and Notes travel back, with a cell counted as your edit only
when it differs from what the last push wrote there — otherwise a stale cell re-applies itself
over the database's own progress every night.

---

## Phase 2 — Assisted apply, Workday included — **done**

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

**Built:** shared field-map layer with the matching in Python, per-tenant credential vault on
the OS keychain, resume-parse-correction diffing, confirmation-page detection, and the resume
served to the extension over localhost so it can attach it to a file input. 26 extension tests
run the real content scripts against real DOMs.

**Cost of Workday-first, stated plainly:** the Workday adapter is roughly a week and a half
against two or three days for the other three combined, so it pushes this phase from about one
week to about two and a half. It is worth it here — the banks, their AI labs, the telecoms and
the insurers are where much of Canada's senior Python and AI hiring sits, and the per-tenant
account friction it removes is the worst part of applying there. Build the easy three first inside
the phase so you are applying for real within days rather than waiting out the Workday work.
Partial fill (steps 1–2, then hand over) counts as done for the first pass.

---

## Phase 3 — Scoring and tailoring — **done**

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

**Done:** deterministic extraction, shape classification, scoring with a seniority penalty,
a gate that records its skip reasons, JD caching by description hash, and constrained rephrasing
behind an optional model call that is discarded whenever it fails re-validation. The gap report
is honest about what is missing rather than papering over it.

---

## Phase 4 — Email triage — **done**

- Gmail read-only OAuth; incremental sync on `history.startHistoryId`.
- Classifier + extractor; assessment deadlines and interview slots pulled out.
- Email → application matcher with the orphan bucket.
- Status write-back to DB and sheet.
- 08:00 digest; instant push for interview / assessment / offer.
- `ghosted` auto-transition at 21 days.

**Done:** rule-based classification with quoted-history stripping, deadline extraction,
domain/name/title matching with an explicit orphan bucket, forward-only status movement,
21-day ghosting, and a digest that leads with whatever is time-critical. Gmail access is
read-only and incremental; the transport for an instant push is the one piece still to wire
up, and the sweep already reports which items warrant one.

---

## Phase 5 — Discovery — **done**

- Canadian company watchlist with detected `ats_type`, seeded from the Toronto, Waterloo,
  Montreal and Vancouver tech and AI ecosystems.
- Nightly pull from public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters) — these are free, documented, and pleasant to consume.
- Job Bank (jobbank.gc.ca) postings feed — confirm the current feed format before building
  against it. High volume, mixed tech signal, so it leans hard on the score gate.
- Feed-based sources for Canada-remote roles.
- Upsert on `dedupe_key`; score everything; queue the top N for tailoring.
- Digest section: "new, scored above threshold, tailored and ready."

**Done:** board readers for Greenhouse, Lever, Ashby, Workable, SmartRecruiters and
Recruitee with drift-tolerant field mapping; title and location filters; capture through the
same deduplicating path as the extension; scoring and gating on everything new; and resumes
rendered for the best-scoring jobs of the night. Job Bank remains unwired — its feed format
needs confirming from a machine that can reach it.

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
