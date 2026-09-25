# Architecture

## 1. Reality check on the original plan

Four parts of the original idea will fail in practice. Each has a better version.

| Original | Why it breaks | Do this instead |
|---|---|---|
| Fully automated, unattended applying | Bot-submitted applications get accounts banned on LinkedIn / Naukri / Indeed, and unreviewed answers to "why this company" are visibly generated. One bad batch poisons your name at 50 companies at once. | **Assisted apply.** The system fills every field, attaches the tailored resume, drafts the free-text answers, and highlights what it touched. You scan it and press submit. ~8 seconds per application instead of ~8 minutes, with none of the risk. |
| Bot-scrape the job boards | LinkedIn actively detects and bans; Indeed and Naukri rate-limit and cloak. Selenium logins are the fastest route to a locked account. | **Three legitimate sources:** (a) the extension captures whatever you are already looking at, in your own logged-in session; (b) a nightly pull from free public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) for a watchlist of companies; (c) official feeds and APIs where they exist. |
| Google Docs holds the resume | You cannot control how a Docs export parses in an ATS, cannot diff two versions, cannot programmatically guarantee a single-column layout. Docs is a rendering target, not a data model. | **`profile.yaml` fact bank** → tailoring selects and orders facts → renderer emits an ATS-safe PDF and DOCX. A Google Doc copy is written *out* for human reading only. |
| Google Sheets is the database | Three writers (extension, nightly worker, you) against a spreadsheet produces lost updates and duplicated rows. There is no transaction and no unique constraint. | **SQLite as source of truth** (Postgres when it outgrows one machine), with Sheets as a two-way-synced mirror. The worker pushes everything; only `Status` and `Notes` sync back from Sheets. |

Everything else in the original idea is right, and the instinct to capture jobs through an
extension in your own browser session rather than a headless scraper is the correct one.

## 2. Components

```
┌─────────────────────────────┐
│ Chrome extension (MV3)      │
│  • capture adapters         │  "Save this job"  ──┐
│  • side-panel job queue     │                     │
│  • autofill engine          │  fills the form     │
│  • resume drag-and-drop     │  from the panel     │
└─────────────────────────────┘                     │
                 ▲                                  ▼
                 │  jobs, resume file, field map   ┌──────────────────────────┐
                 └─────────────────────────────────┤ API  (FastAPI, local)    │
                                                   │  • the only holder of    │
┌─────────────────────────────┐   read/write       │    secrets and OAuth     │
│ Worker (cron)               ├───────────────────►│    tokens                │
│  • 02:00 board discovery    │                    │  • job/app CRUD          │
│  • 03:00 tailoring queue    │                    │  • tailoring endpoint    │
│  • 07:30 Gmail sweep        │                    │  • autofill plan builder │
│  • 08:00 digest             │                    └────────────┬─────────────┘
└─────────────────────────────┘                                 │
                                                                ▼
                                      ┌──────────────────────────────────────┐
                                      │ Store                                │
                                      │  SQLite (state) + resumes/ (files)   │
                                      └──────────────────────────────────────┘
                                                                │
                              ┌─────────────────────────────────┼──────────────┐
                              ▼                                 ▼              ▼
                       Sheets mirror                     Google Docs      daily digest
                    (dashboard you can edit)          (readable resume)  (email/Telegram)
```

**Why a backend at all, rather than putting it in the extension:** the extension cannot
safely hold an LLM API key or a Gmail OAuth refresh token — anything shipped in an extension
bundle is readable by anyone who installs it, and Chrome's storage is not a secret store. The
backend also gives you cron, a real database, and the ability to swap the extension out later
without losing your history.

Run the API on `127.0.0.1` for v1. It is a single-user system; do not expose it publicly
until it has auth.

## 3. Data model

```
companies      id, name, name_norm, domain, ats_type, careers_url, watchlist(bool)
jobs           id, company_id, title, title_norm, location, remote_type, seniority,
               description_raw, description_hash, apply_url, canonical_url, source,
               posted_at, discovered_at, salary_min, salary_max, dedupe_key, is_open

applications   id, job_id, status, match_score, resume_version_id, applied_at,
               last_status_change_at, next_action, next_action_due, notes, sheet_row

resume_versions id, job_id, profile_revision, selected_fact_ids(json), pdf_path,
               docx_path, gdoc_url, keyword_coverage(json), created_at

answers        id, question_norm, answer_text, scope(global|company|role), times_used
               -- the reusable answer bank: notice period, visa, expected CTC, relocation…

events         id, application_id, kind, source, occurred_at, payload(json)
               -- every status change, email match, autofill run, and manual edit

email_links    id, gmail_message_id, gmail_thread_id, application_id, classification,
               confidence, extracted(json), reviewed(bool)
```

**Dedupe key** — the same job appears on LinkedIn, the company site, and a job board.
`dedupe_key = sha1(company_name_norm + "|" + title_norm + "|" + location_norm)`, where
normalisation lowercases, strips punctuation, drops seniority noise (`Sr.` → `senior`), and
canonicalises locations (`Bengaluru`/`Bangalore`/`BLR` → one token). A second `description_hash`
catches reposts of the same listing under a new ID.

**Application state machine:**

```
discovered ──► scored ──► tailored ──► ready ──► applied ──► acknowledged
                  │                      │                        │
                  └──► skipped           └──► abandoned           ├──► screening
                       (score gate,                               ├──► assessment
                        or you pass)                              ├──► interview
                                                                  ├──► offer
                                                                  ├──► rejected
                                                                  └──► ghosted
                                                                       (auto at 21d
                                                                        of silence)
```

Only `applied`, `rejected`, `offer` and manual overrides are terminal-ish. `ghosted` is an
automatic transition so your pipeline reflects reality instead of 200 rows of false hope.

## 4. The resume pipeline

This is the part most such projects get wrong, so it is specified tightly.

### `profile.yaml` — the fact bank

```yaml
identity:   { name: …, email: …, phone: …, links: {…} }
constraints: { notice_period_days: 60, work_auth: …, expected_ctc: …, locations: [...] }

facts:
  - id: f_pay_latency
    role: backend-eng-acme          # which job in your history it belongs to
    bullet: "Cut p99 checkout latency 820ms → 180ms by replacing N+1 ORM reads
             with a batched loader and a Redis read-through cache."
    metrics: [latency, p99, redis, caching, sql]
    skills: [python, redis, postgresql, performance]
    aliases: { caching: [redis, memcached, read-through cache] }
    strength: 5                     # how proud you are of it, 1-5
```

Every bullet you could ever put on a resume lives here exactly once, tagged. Nothing else
is a legitimate source of resume content.

### Tailoring, constrained

1. **Extract** the JD into structured requirements (must-have / nice-to-have / keywords /
   seniority signals). Cache by `description_hash` — the same JD is never parsed twice.
2. **Score** fit: weighted overlap of JD must-haves against your `skills` and `facts`. This
   runs *before* any expensive call. Below the threshold, the job never reaches tailoring.
3. **Select** facts: rank by relevance × strength, fill the page budget, guarantee coverage
   of every must-have you can genuinely support.
4. **Rephrase** — and only rephrase. The model may reword a selected bullet to use the JD's
   vocabulary where an `aliases` entry licenses it. It may not add a bullet, a skill, a date,
   or a number.
5. **Validate**, mechanically: every rendered bullet must carry its `fact_id`; every number in
   the output must appear in the source fact; the skills section must be a subset of
   `profile.yaml` skills. A tailored resume that fails validation is rejected, not shipped.
   This check is what makes the whole system safe to run at volume.
6. **Gap report:** JD keywords you genuinely cannot support are surfaced as a gap list, which
   feeds the skip decision and your own learning list. It is never an instruction to fabricate.

### Rendering

One source, two outputs, because Indian portals (Naukri, Instahyre) and some ATSs want DOCX
while everything else wants PDF.

ATS-safe rules the renderer enforces: single column; no tables, text boxes, icons, or
multi-column headers; contact details in the body, never in a page header; standard section
names (`Experience`, `Education`, `Skills`, `Projects`); a real text layer with embedded
standard fonts; no images. Verify by running the generated PDF back through a text extractor
and asserting the extracted text matches what you rendered — a one-line test that catches
most parsing disasters.

File naming: `Aakash_Dahiya_{Company}_{Role}.pdf`, with the version row keeping the mapping.

## 5. The autofill engine

**Adapter per ATS, not a generic guesser.** A generic label-matcher works about 60% of the
time, which is worse than useless because you stop trusting it. Detect the ATS from the URL
and a DOM fingerprint, then run its adapter.

Build order by coverage-per-effort:

1. **Greenhouse, Lever, Ashby** — stable DOM, single-page forms, standard file inputs. Covers
   most startup and mid-market roles. Start here.
2. **Workable, SmartRecruiters, Zoho Recruit** — same shape, slightly messier.
3. **Naukri / Instahyre / Foundit** — mostly profile-based rather than per-job forms; the win
   here is keeping the hosted profile in sync, not filling a form.
4. **Workday, Taleo, iCIMS** — multi-step, stateful, account-per-company. Real work. Defer until
   the rest is solid, and treat "fill step 1 and 2, hand over" as a legitimate win.

**Mechanics:**
- The panel holds the tailored resume ready to drag onto the form's file input, and can also
  set the `<input type=file>` directly via `DataTransfer` where the site allows it.
- Field mapping is `label-regex → profile key`, with per-adapter overrides and a learning
  loop: when you correct a filled field, the correction is stored and reused.
- The recurring dozen questions (notice period, expected CTC, visa, relocation, referral,
  "how did you hear about us") come from the `answers` bank, not from a model.
- Genuinely novel free-text ("why this company") is drafted, then visibly marked as a draft.
  Anything the system wrote is outlined so you know what to actually read.
- **It never clicks submit.** After you submit, the adapter detects the confirmation page or
  URL change and marks the application `applied` automatically. This is the right way to get
  the checkmark — far more reliable than remembering to press a button, and it is why the
  tracker stays accurate.
- Every autofilled value is written to `events` as an audit trail.

**Double-apply guard:** a unique constraint on `(company_id, title_norm)` within a 60-day
window. The panel shows a hard warning before you can proceed on a repeat.

## 6. Email triage

- **Gmail API, read-only scope**, incremental sync via `history.startHistoryId`. Never poll
  the whole mailbox; a sweep should read a handful of new messages, not thousands.
- **Classify** into `rejection | interview_invite | assessment | recruiter_outreach | offer |
  acknowledgement | other`. A cheap model or even a good rule set handles most of it; the
  expensive model only sees the ambiguous ones.
- **Extract** the time-critical things: assessment deadline, interview slot options,
  recruiter's asks. An online-assessment link with a 72-hour window is the single most
  expensive thing to miss, so it gets an instant push rather than waiting for the digest.
- **Match** email → application: sender domain against `companies.domain`, then thread subject
  fuzzy match against job title, then a model tiebreak. Anything unmatched goes to an orphan
  bucket you clear in the digest — never guessed at.
- **Write back:** update `applications.status`, append an `events` row, update the Sheets cell.
- **Privacy:** send the subject plus the first ~500 characters to the model, not whole threads.
  Keep the store local. Redact attachments entirely.

**Output:** one 08:00 digest — new matches worth applying to, applications going stale,
anything needing a reply, and the orphan bucket. Plus immediate pushes for
`interview_invite`, `assessment`, and `offer`.

## 7. Guardrails

- **Rate limits and politeness:** respect `robots.txt`, one request per second per host,
  identify the crawler honestly, only touch public endpoints. No headless logins, ever.
- **Score gate before spend:** no tailoring below the match threshold. This is both a cost
  control and a quality control.
- **Cost model:** cheap model for JD extraction and email classification (the high-volume
  paths, and both cacheable); strong model only for tailoring and novel free-text. Cache JD
  extraction by `description_hash`. Batch the nightly work.
- **Idempotency everywhere:** every worker run is safe to re-run. Discovery upserts on
  `dedupe_key`; the Gmail sweep is keyed on `gmail_message_id`.
- **Quality ceiling:** cap applications per day. Fifteen well-targeted applications beat
  ninety sprayed ones, and the response rate difference is not subtle.
- **Audit everything:** `events` is append-only. When you wonder "why does it think I applied
  to this," the answer is in the table.

## 8. Stack

| Layer | Choice | Why |
|---|---|---|
| Extension | MV3, TypeScript, React in the side panel | Side panel (not popup) so the job queue stays open while you fill forms. |
| API | Python + FastAPI | Best ecosystem for the Google, Gmail and LLM clients you need; Pydantic gives you the JD-extraction schema for free. |
| DB | SQLite via SQLAlchemy, Alembic migrations | Single user, single machine. The migration path to Postgres is then trivial. |
| Resume render | Typst → PDF; `python-docx` from a template → DOCX | Typst is fast, scriptable, and produces a clean text layer. LaTeX also works if you already know it. |
| Scheduling | `cron` calling the worker CLI | Do not build a scheduler. |
| Sheets/Docs/Gmail | Google API Python client, one OAuth consent | Sheets and Docs are views; Gmail read-only. |
| Notifications | Telegram bot, or email to yourself | Telegram is the least friction for instant pings. |

## 9. Deliberately out of scope

Auto-submitting applications. Auto-replying to recruiters. Headless logins to job boards.
Multi-user support. Anything that writes to Gmail. Each of these is either a ban risk, a
reputational risk, or work that buys nothing until the core loop is running.
