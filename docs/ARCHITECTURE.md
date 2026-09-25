# Architecture

## 1. Reality check on the original plan

Four parts of the original idea will fail in practice. Each has a better version.

| Original | Why it breaks | Do this instead |
|---|---|---|
| Fully automated, unattended applying | Bot-submitted applications get accounts banned on LinkedIn / Indeed / Glassdoor, and unreviewed answers to "why this company" are visibly generated. One bad batch poisons your name at 50 companies at once. | **Assisted apply.** The system fills every field, attaches the tailored resume, drafts the free-text answers, and highlights what it touched. You scan it and press submit. ~8 seconds per application instead of ~8 minutes, with none of the risk. |
| Bot-scrape the job boards | LinkedIn actively detects and bans; Indeed and Glassdoor rate-limit and cloak. Selenium logins are the fastest route to a locked account. | **Three legitimate sources:** (a) the extension captures whatever you are already looking at, in your own logged-in session; (b) a nightly pull from free public ATS JSON endpoints (Greenhouse, Lever, Ashby, Workable, SmartRecruiters) for a watchlist of companies; (c) official feeds and APIs where they exist. |
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
jobs           id, company_id, title, title_norm, locations(json), remote_type, seniority,
               description_raw, description_hash, apply_url, canonical_url, source,
               posted_at, discovered_at, salary_min, salary_max, dedupe_key, is_open
               -- locations is a SET: enterprises post one req across several cities

applications   id, job_id, status, match_score, resume_version_id, applied_at,
               last_status_change_at, next_action, next_action_due, notes, sheet_row

resume_versions id, job_id, profile_revision, selected_fact_ids(json), pdf_path,
               docx_path, gdoc_url, keyword_coverage(json), created_at

answers        id, question_norm, answer_text, scope(global|company|role), times_used
               -- the reusable answer bank: work authorisation, notice period,
               --   expected base salary, relocation, referral source…

events         id, application_id, kind, source, occurred_at, payload(json)
               -- every status change, email match, autofill run, and manual edit

email_links    id, gmail_message_id, gmail_thread_id, application_id, classification,
               confidence, extracted(json), reviewed(bool)

ats_accounts   id, company_id, ats_type, tenant_url, username, secret_ref,
               profile_last_synced_at, last_application_id, notes
               -- one row per Workday/Taleo tenant, because each employer is a separate
               -- account. secret_ref points into the OS keychain; there is no password
               -- column, and credentials never touch the database or the repo.
```

**Dedupe key** — the same job appears on LinkedIn, the company site, and a job board, and
**location is deliberately not part of the key**:

`dedupe_key = sha1(company_name_norm + "|" + title_norm)`

Normalisation lowercases, strips punctuation, and drops seniority noise (`Sr.` → `senior`).
A second `description_hash` confirms the match and catches reposts of the same listing under a
new ID.

Leaving location out is the right call for a Canada-wide search, because enterprises — Workday
tenants especially — publish one requisition separately for Toronto, Vancouver, Calgary and
Montreal. Keying on location turns one job into five rows and five near-identical tailored
resumes. Instead, a matching company + title + description hash collapses into **one job with a
set of locations**, and city-name variants (`Toronto, ON`/`Greater Toronto Area`/`GTA`;
`Montréal`/`Montreal`/`MTL`; `Remote - Canada`/`Remote (Canada)`) canonicalise inside that set.
The rare false collapse — a genuinely different role sharing a title at one company — is caught
by the description hash and is much cheaper than the duplicate flood it prevents.

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
constraints:
  work_auth:         work_permit
  permit_type:       open                       # PGWP — derives the sponsorship answer, §9
  permit_subtype:    pgwp
  work_auth_expiry:  YYYY-MM-DD                 # >2 years out, so no start-date flagging
  citizenship:       India
  credential_assessment: null                   # WES ECA reference, if you hold one
  provinces:         [canada-wide]
  work_modes:        [remote, hybrid, onsite]
  relocate_within_canada: true
  expected_base_cad: { min: …, target: … }
  notice_period_days: 30
  french_level:      none

self_id:                        # voluntary, disclosed; canonical values — §5 maps per ATS
  mode:             disclose
  gender:           man
  indigenous:       no
  racialized:       yes
  racialized_group: south_asian
  disability:       no
  veteran:          no

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

One source, two outputs. PDF is the default; DOCX matters more in the Canadian market than you
might expect, because recruitment agencies and some enterprise Workday/Taleo instances still ask
for it, and a few parse it more reliably than PDF.

**The renderer reproduces the existing resume's format**, since that format is already good:
Calibri 10pt, 0.5in/0.6in margins, a 17pt name, 11pt bold section headings over a hairline rule, a
per-role `Tech:` line, right-aligned dates, and Projects as a first-class section. Bullets may bold
a phrase with `**markup**`; every content check runs on the stripped text, so markup can never
carry a claim past a validator.

**Canadian conventions it also enforces:** no photo, no date of birth, no marital status, and
never a SIN — including these reads as unfamiliarity with the market and creates a
human-rights-compliance problem for the employer. Location as `City, ON` style. Two pages is
normal and accepted here, so the page budget is not one page.

**On stating work authorisation:** on an open work permit, one line near the top — "Authorised to
work in Canada, open work permit valid to <date>" — removes the most common screening doubt on a
Canadian application, and it is simply true. On an employer-specific permit the same line invites
a question you would rather answer in conversation than have screened on, so leave it off the
resume and handle it in the application's own authorisation fields. `permit_type` therefore drives
the renderer as well as the answer bank.

ATS-safe rules the renderer enforces: single column; no tables, text boxes, icons, or
multi-column headers; contact details in the body, never in a page header; standard section
names; a real text layer with embedded standard fonts; no images; hyphenation off so extracted
text matches the source exactly. The generated PDF is run back through a text extractor and the
result checked against what was rendered.

**One finding worth recording.** Right-aligned dates — the tab-stop style the source resume uses,
and the style most resumes use — are positioned correctly in the PDF, but two of three tested
extraction heuristics group the date into the *following* paragraph, so a bullet arrives at the
employer as "…with an online quote wizard and photo **Mar 2026 – Present** uploads." Strict
position-order extraction reads it correctly, so a layout-aware parser is fine and a naive one is
not. It is a coin flip taken for free. The renderer therefore keeps the right-aligned style by
default and offers `--date-style inline`, which removes the ambiguity entirely; a test asserts the
inline variant carries no such risk, and an advisory prints whenever the default one does.

File naming: `Aakash_Dahiya_{Company}_{Role}.pdf`, with the version row keeping the mapping.

## 5. The autofill engine

**Adapter per ATS, not a generic guesser.** A generic label-matcher works about 60% of the
time, which is worse than useless because you stop trusting it. Detect the ATS from the URL
and a DOM fingerprint, then run its adapter.

Build order, with **Workday first** per your call:

1. **Workday** — the largest single share of senior Python and AI hiring in the Canadian
   enterprise market: the big five banks and their AI labs, the telecoms, the large insurers.
   Also by far the hardest, so it gets its own subsection below.
2. **Greenhouse, Lever, Ashby** — stable DOM, single-page forms, standard file inputs. Cover
   most scale-up and AI-lab roles. Build these *in the same phase* as Workday, not after: they
   are cheap once the field-map layer exists, and they give you working applications in week one
   while the Workday adapter is still maturing.
3. **Workable, SmartRecruiters, Zoho Recruit** — same shape as the group above, slightly messier.
4. **Taleo** — still common at Canadian banks and older enterprises. Shares Workday's
   account-per-employer problem without sharing its selector discipline.
5. **iCIMS, BambooHR, Dayforce** — the long tail. Only worth it once you see the same one twice.
6. **GC Jobs (jobs.gc.ca)** — the federal public service runs its own portal with a persistent
   applicant profile, bilingual-requirement fields, and screening questions that must be
   answered in prose. Profile-sync problem, not a form-fill problem. Lowest priority unless
   you are targeting federal roles.

### Workday, specifically

Workday inverts the usual autofill problem, and the adapter has to be built around that fact.

**It parses your resume and then fills the form itself — badly.** The flow is: upload resume →
Workday auto-populates work history, education and skills from its own parse → the parse is
routinely wrong about dates, employer names, and bullet boundaries. So the adapter's real job is
not filling empty fields, it is **diffing Workday's parsed state against `profile.yaml` and
correcting the deltas**. Build it that way from the start; a fill-the-blanks design will fight the
platform the whole way.

**Every employer is a separate tenant and a separate account.** URLs look like
`<employer>.wdN.myworkdayjobs.com/<site>`, and each one wants its own username and password. This
is the single largest source of friction in Canadian enterprise applying, and therefore the
single largest win available: the `ats_accounts` table plus an OS-keychain vault means you never
reset a Workday password again. Credentials live in the keychain, referenced by `secret_ref` —
never in the database, never in the repo, never in extension storage.

**The good news: selectors are stable.** Workday annotates its DOM with `data-automation-id`
attributes, which are far more reliable to target than its generated CSS classes. One adapter
generalises across tenants because the widget set is shared. Verify the specific ids against two or
three live tenants before trusting them, and re-verify after Workday releases — they do change.

**Widgets are not native controls.** Dropdowns are custom listboxes, not `<select>`, and typeahead
fields need a real input event followed by an option click. Setting `.value` directly does nothing.
Plan for click-then-pick helpers as a shared primitive.

**The wizard is multi-step and stateful:** My Information → My Experience → Application Questions
→ Voluntary Disclosures → Self Identify → Review. Each step saves separately and any of them can
reject and bounce you back. The adapter must therefore be **resumable** — store per-application
step progress, so an interrupted application continues instead of restarting.

**Use the platform's own shortcut.** Within a tenant, Workday offers to reuse your last
application. When `ats_accounts.last_application_id` is set for that company, take it — it is
faster and more accurate than anything the adapter can do, and it leaves you only the
job-specific answers and the tailored resume to swap in.

**Honest cost:** this adapter is roughly a week and a half on its own, against two or three days
for Greenhouse, Lever and Ashby combined. Building it first delays your first fully-assisted
application by about a week. That is why the easy three ship in the same phase — you get a
working loop immediately and the Workday work lands on top of a proven field-map layer rather
than inventing one.

**Mechanics:**
- The panel holds the tailored resume ready to drag onto the form's file input, and can also
  set the `<input type=file>` directly via `DataTransfer` where the site allows it.
- Field mapping is `label-regex → profile key`, with per-adapter overrides and a learning
  loop: when you correct a filled field, the correction is stored and reused.
- The recurring dozen come from the `answers` bank, not from a model. In the Canadian market
  that set is: are you legally entitled to work in Canada (yes); do you require sponsorship
  (derived from `permit_type`, never hardcoded — see §9); permit type and expiry; country of
  citizenship; willingness to relocate within Canada (yes) and acceptable work modes; French
  proficiency; expected base salary in CAD; notice period; referral source; "how did you hear
  about us".
- **Voluntary self-identification is filled only from the explicit `self_id` block**, never
  inferred from anything else in your profile. Disclosure is currently on. The block stores
  *canonical* values and each adapter maps them to that ATS's own option strings, because the
  wording diverges more than you would expect: a Canadian employer asks about **visible minority
  / racialized person** under employment-equity categories where `south_asian` is a listed group,
  while a US-headquartered company hiring into Canada often ships US EEO-1 wording instead — a
  coarser race/ethnicity list, a separate Hispanic/Latino question, veteran status defined against
  the US armed forces, and disability asked through form CC-305. A single stored string cannot
  serve both, so the mapping lives per adapter.
- **Where no confident mapping exists, mark and skip** rather than guess at a category. Same for
  accommodation-request fields, which are situational and stay yours to answer.
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
- **Credentials never leave the keychain.** Workday and Taleo accounts are referenced by
  `secret_ref` only. No password columns, nothing in extension storage, nothing in the repo, and
  no credentials in `events` payloads.
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

## 9. Market profile: Canada (AI / tech / Python)

The target market is Canadian AI, software and Python roles. That shapes four things.

### Discovery sources, in priority order

1. **Public ATS feeds for a Canadian company watchlist.** Greenhouse, Lever, Ashby, Workable and
   SmartRecruiters each expose a free, documented JSON endpoint per company board. Build a
   watchlist of Canadian tech and AI employers, detect each one's `ats_type` once, then poll
   nightly. This is the highest-signal, lowest-risk source available and it costs nothing.
   Seed the watchlist from the Toronto / Waterloo / Montreal / Vancouver ecosystems and the
   AI-lab cluster around Vector, Mila and Amii.
2. **Job Bank (jobbank.gc.ca).** The federal job board publishes machine-readable postings.
   Verify the current feed format before building against it rather than trusting any
   documentation from memory. Volume is high and tech-role signal is mixed, so it needs the
   score gate more than the ATS feeds do.
3. **The extension, on whatever you browse** — LinkedIn, Indeed Canada, Glassdoor, and the
   ecosystem boards (Communitech, MaRS, Vector's job board, TechTO). Capture only; no crawling.
4. **Company careers pages** for employers that run neither a known ATS nor a feed. Last resort,
   one polite request a day.

### Work authorisation: work permit, no sponsorship required

Your status is a work permit with no sponsorship needed, which turns the discovery filter into
nearly a no-op — postings that say "must be legally entitled to work in Canada without
sponsorship" stay in scope. Two things still need encoding, and the first is a trap.

**Settled: PGWP, which is an open permit.** You can work for any employer, so "no, I do not
require sponsorship" is accurate, and the resume carries the authorisation line. `permit_type`
stays a field rather than a hardcoded answer, because the logic differs sharply for the closed
case: on an **employer-specific** permit, changing employers needs a new permit and often an
LMIA, which is functionally what employers mean by sponsorship, and answering "no" there surfaces
at the offer or background-check stage. The answer bank therefore **derives** the sponsorship
answer from `permit_type` instead of storing a flat "no".

**Expiry: more than two years out, so flagging is off.** No start-date warnings and no long-cycle
employer flags — banks and insurers with four-month processes are fully in scope. Keep
`work_auth_expiry` populated anyway, because a PGWP is single-use and non-renewable: the date is a
wall, not a renewal, and the flagging logic should switch itself on as the window closes rather
than need building later.

**Constant answers** that go in the bank on day one: legally entitled to work in Canada — yes;
requires sponsorship — derived from `permit_type`; permit type and expiry; country of citizenship
— India.

**Credential equivalency.** A degree earned in India occasionally draws a "Canadian equivalency"
question. If you hold a WES ECA, put its reference in `credential_assessment`; if not, the honest
answer is the degree as awarded, which is rarely a blocker in tech.

**Never store or autofill a SIN**, and never put one in `profile.yaml`.

### Location: Canada-wide, and what that costs

Canada-wide with relocation, and remote or hybrid both acceptable, means the location filter is
effectively off. That simplifies the adapters, but it has two consequences worth building for now
rather than patching later:

- **Volume rises sharply.** The score gate stops being a cost optimisation and becomes the main
  thing keeping the queue usable. Expect to tune the threshold during the first week of real use,
  and to want a per-day application cap sooner than you think.
- **Multi-city requisitions must collapse.** This is why `dedupe_key` excludes location (§3).
  Without that, a single Workday req posted for four cities becomes four rows and four
  near-identical tailored resumes — and four chances to apply twice to the same job.

### Compensation and language

Salary expectations are in **CAD base**, and Canadian postings mix annual and hourly rates, so the
`answers` bank stores both forms and the adapter picks by field type. Quebec roles and federal or
federally-regulated postings may require French; `french_level` drives both a scoring adjustment
and an honest answer rather than an optimistic one.

### Role-shape notes for AI and Python work

The shapes were revised once the real resume was in hand, because the generic taxonomy did not
fit the history. There are no publications and no large-scale serving platform, so tagging facts
for a "research" or "ML platform" shape would have meant inventing evidence. The three shapes the
history genuinely supports are **ai_engineer** (LLM products, RAG, vision, embeddings),
**backend_python** (FastAPI services, data modelling, the layer underneath), and **fullstack**
(Next.js/Supabase product surfaces shipped end to end). Each fact carries a `shapes` field.

This is the general rule, not a one-off: the taxonomy follows the fact bank. A shape nothing can
be tagged for is a shape you cannot apply to honestly.

**One shape per application, never three.** The obvious failure mode of covering all three is a
resume that reads unfocused to every one of them. The pipeline resolves it mechanically rather than
by compromise: a **shape classifier** runs on the JD during extraction and assigns it one primary
shape, and the tailorer then selects facts weighted to *that* shape only. The breadth lives in the
bank; every rendered resume is single-shaped. This is the main reason the fact bank and the
renderer are separate stages.

**Depth check, because breadth is only free if it is real.** At build time, count facts per shape.
Any shape too thin to fill a resume gets a warning rather than silent thin output. On the current
fact bank that check earns its keep immediately: `fullstack` is well covered, while `ai_engineer`
rests on four bullets — which matters, because the AI roles are the target. The honest fix is
another shipped AI project, not a lower threshold.

## 10. Deliberately out of scope

Auto-submitting applications. Auto-replying to recruiters. Headless logins to job boards.
Multi-user support. Anything that writes to Gmail. Each of these is either a ban risk, a
reputational risk, or work that buys nothing until the core loop is running.
