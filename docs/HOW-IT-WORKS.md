# How it works

The whole tool, end to end. For installation follow [`SETUP.md`](SETUP.md); for *why* each
decision was made, [`ARCHITECTURE.md`](ARCHITECTURE.md); for what was built when,
[`ROADMAP.md`](ROADMAP.md).

---

## What this is

A job application system for Canadian AI, tech and Python roles. It does four things:

1. **Captures** postings you find, or that a nightly crawl finds, into one deduplicated list.
2. **Scores** each one against your history and skips the ones not worth your evening.
3. **Tailors** a resume to each posting it keeps — from a fact bank, never from imagination.
4. **Tracks** every application, moving it through its states from your inbox automatically.

It never submits an application. You review and press submit; everything up to that moment is
the part it removes.

## What it is not

Not a bulk applier. Not a LinkedIn scraper — see [Why no bulk scraping](#why-no-bulk-scraping).
Not a resume writer that invents experience; it physically cannot, and the section on the
[fact bank](#the-fact-bank-and-why-it-cannot-lie) explains the mechanism.

---

## The pieces

Three things run, and only the first two are yours to start.

| Piece | What it is | Where |
|---|---|---|
| **The API** | FastAPI on `127.0.0.1:8765`. The brain and the database. | `api/` |
| **The extension** | Chrome MV3. Hands: reads pages, fills forms, shows the queue. | `extension/` |
| **The workers** | CLIs run by hand or by cron: crawl, inbox, sheets. | `discover/`, `inbox/`, `sheets/` |

The extension holds no logic worth the name. It reports what it can see on a page and does what
the API tells it. That is deliberate: a new ATS then needs new *selectors*, not new rules, and
every rule stays testable without a browser.

```
  LinkedIn / Indeed / a careers page
            │  ☆ Save, or Ctrl+Shift+S
            ▼
   ┌──────────────────┐        ┌──────────────────────┐
   │    extension     │◄──────►│   API  :8765         │
   │  capture / fill  │        │   SQLite, the truth  │
   └──────────────────┘        └──────────┬───────────┘
            ▲                             │
            │ prompt / reply              │ score → tailor
            ▼                             ▼
      claude.ai tab                  data/resumes/*.pdf
                                          │
   Gmail ──► triage ──► status ──► digest ┘  + instant push
```

---

## The daily loop

**Morning.** Read the digest (`python -m inbox.run --digest`). It leads with whatever is
time-critical — an assessment with a deadline, an interview to book — then what is ready to
apply to, what moved overnight, what is going quiet, and anything it could not place.

**While browsing.** On a LinkedIn or Indeed search, every job card carries a **☆ Save**. One
click stores it with its apply link. On any other job page, **Ctrl+Shift+S** (**Cmd+Shift+S**
on a Mac) or **Save this job** in the side panel.

**Per job, in the side panel.**

1. **tailor** — writes that posting's resume (see [Tailoring](#tailoring)).
2. **resume ↓** appears — open it, or drag it straight onto the employer's upload box.
3. **apply** — opens the posting and points the next fill at this application.
4. **Fill this form** — fills what it can, attaches the right resume, outlines what it touched.
5. You check it and submit. The confirmation page moves the status to `applied` by itself.

**Overnight, if you have cron set up.** The crawl reads the watchlist's job boards, keeps what
fits, scores everything new, and renders resumes for the best of them. The inbox sweep triages
replies and moves applications. The sheet mirror pushes rows out and pulls your edits back.

---

## Capture

Saving a posting takes what it can get, in order of how much it can be trusted:

1. **schema.org `JobPosting` JSON-LD** — the page's own machine-readable description.
2. **A site adapter** — Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, LinkedIn,
   Indeed — for whatever the JSON-LD lacked.
3. **Open Graph and `<title>`** — so an unknown careers page still captures something.

From a results card there is no description; it lives on the detail page. So the save lands
immediately and the worker fetches the detail page afterwards to fill it in, re-posting through
the same deduplicating path. If that fetch hits a login wall the job is still saved, with
`has_description: false`, and the panel shows it as not yet ready rather than offering to tailor
something it cannot read.

**Dedupe is the part that matters.** `Sr. ML Eng @ Cohere Inc.` in Toronto and
`Senior Machine Learning Engineer @ cohere` in Vancouver are **one job with two locations**, not
two rows. Location is deliberately out of the dedupe key. Re-capturing something you already
applied to warns you instead of letting you apply twice.

## Scoring

Before anything is spent on a posting, `tailor/score.py` decides whether it is worth it: which
required terms match, which are missing, a seniority penalty when the years asked for exceed the
years you have. Below the threshold it is `skipped` **with the reason recorded** — without that,
the same posting gets reconsidered from scratch every time it reappears from another source.

A **shape classifier** assigns one of `ai_engineer`, `backend_python` or `fullstack` to each
posting. Breadth lives in the fact bank; every rendered resume is single-shaped.

## Tailoring

Two routes, one gate.

- **Through a chat** (default, no API key). The panel's **tailor** button opens a claude.ai tab,
  pastes the prompt the server built, reads the reply back out of the page, and posts it for
  validation.
- **Through the API** (`--use-model`, needs `ANTHROPIC_API_KEY`). Same prompt, same validation,
  runs unattended for a whole nightly batch.

Both end at `rephrase.accept_or_discard`. That is not a detail — it is the design.

### The fact bank, and why it cannot lie

`profile.yaml` is the only source of resume content. Every bullet carries a `fact_id`. Nothing
reaches a rendered page without one.

When a model rewrites a bullet, the result must survive `resume/verify.py`:

- the same **set** of bullets comes back — none added, none dropped;
- every bullet traces to a real `fact_id`;
- **no number appears that its source fact does not contain**;
- no skill reaches the page unsupported by a selected bullet;
- and after rendering, the text is pulled back **out of the finished PDF** and checked against
  what was meant to be there.

A reply that fails is discarded and the original wording is used. You are told what was thrown
out and why. The outcome of a bad rewrite is an untailored resume, never a false one.

This was tested against a reply claiming *"cut retrieval latency by 87%"* and another inventing
a job at Google. Neither reached the PDF; the rejection read
`invented_number [f_rag]: numbers not present in the source fact: ['87']`.

That last check — extracting text back out of the PDF — earned itself on its first run. It
caught right-aligned employment dates being extracted *mid-bullet*, so a bullet would have
reached an employer as `…with an online quote wizard and photo Mar 2026 – Present uploads.`
The layout stays because a layout-aware parser reads it correctly; `--date-style inline` removes
the risk for anyone who would rather not take it.

## Filling a form

The content script enumerates the fields it can see; `api/autofill.py` decides what belongs in
each. Filled fields are outlined green, corrections amber, "your turn" yellow.

Four things it will not do:

- **Generate motivation text.** "Why this company" comes back as a skip. A generated answer to
  that question is visibly generated.
- **Autofill a password.** Credentials go through the keychain-backed vault only.
- **Guess.** An unmatched label is reported as unmatched. One wrong value teaches you to distrust
  the whole fill, which costs more than a blank field.
- **Disclose anything voluntary** unless `self_id.mode` is `disclose`, and then only by matching
  the options the page itself offers.

**Workday** gets the most attention: it holds much of Canadian enterprise hiring and it inverts
the problem by parsing your resume and filling the form itself, usually getting something wrong.
So the adapter diffs its parse against `profile.yaml` and corrects the differences. Per-tenant
logins live in the OS keychain; the database stores only a reference.

The upload is always named `<Name>_Resume.pdf`. The internal filename carries the company and
the role shape, and sending that to a recruiter advertises that you keep a per-company variant.

## Email triage

Gmail, **read-only**, incremental on `history.startHistoryId` — a sweep reads the handful of
messages that arrived, never the whole mailbox.

Replies are classified, deadlines and interview slots extracted, and each email matched to an
application by sender domain, then subject against job title. Anything it cannot place
confidently goes to an **orphan bucket** you clear in the digest; a misattached rejection closes
the wrong application and hides a live one, so it never guesses.

Status only ever moves **forward**: a stray "we received your application" cannot undo an
interview. Silence becomes `ghosted` after 21 days, because a pipeline of 200 hopeful rows is a
lie.

`interview_invite`, `assessment` and `offer` get an **instant push** — Telegram, email, or a
console default that needs no account. Delivery is stamped on the row, so a re-run never pings
you twice, and a push that failed is still owed: the stamp is written *after* the transport
returns, never before.

## Discovery

A watchlist of 50 Canadian employers, each probed to find which ATS it actually uses rather than
guessing. The nightly crawl reads public ATS JSON endpoints — Greenhouse, Lever, Ashby,
Workable, SmartRecruiters, Recruitee — which are free, documented and stable. Everything new is
captured through the same deduplicating path, scored, and the best of the night get resumes.

### Why no bulk scraping

LinkedIn and Indeed publish no jobs API, block scrapers actively, and prohibit it in their
terms — and it is the account you apply from that pays for it. The ☆ Save covers the same ground
for pages you are already looking at, which is user-initiated and unobjectionable, and the ATS
crawl covers volume. Most LinkedIn and Indeed postings are aggregated from those same ATSs
anyway, so little is lost.

---

## The data model

SQLite, at `data/tracker.sqlite`. Seven tables: `companies`, `jobs`, `applications`,
`email_links`, `sync_state`, `ats_accounts`, `events`.

`events` is **append-only**. When you wonder "why does it think I applied to this", the answer is
in that table.

An application moves through: `discovered` → `scored` → `tailored` → `ready` → `applied` →
`acknowledged` → `screening` → `assessment` → `interview` → `offer`, with `rejected`, `ghosted`
and `skipped` as the ways out.

The Google Sheet is a **mirror, not a database**. The worker pushes every row; only `Status` and
`Notes` travel back, and a cell counts as your edit only when it differs from what the last push
wrote there — otherwise a stale cell re-applies itself over the database's own progress every
night.

---

## Commands

```bash
# Start the API — everything else needs this running
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --reload

# Resume pipeline
.venv/bin/python -m resume.render --profile profile.yaml --depth-only
.venv/bin/python -m resume.render --profile profile.yaml --shape ai_engineer --out out/cv.pdf

# Score and tailor one posting from a file
.venv/bin/python -m tailor.run --jd jd.txt --title "Senior AI Engineer"

# Discovery
.venv/bin/python -m discover.detect --out discover/detected.yaml
.venv/bin/python -m discover.crawl --tailor-top 5

# Inbox
.venv/bin/python -m inbox.run --sync --ghost --digest
.venv/bin/python -m inbox.run --notify

# Sheets mirror
.venv/bin/python -m sheets.run --spreadsheet "$TRACKER_SPREADSHEET_ID"

# Staying current
./scripts/update.sh
./scripts/install-autoupdate.sh
```

## Security posture

- The API binds to `127.0.0.1` and has **no authentication**. It serves your profile data and,
  on request, stored ATS passwords. Do not expose it. Adding auth is part of the VPS move, not
  an afterthought to it.
- `profile.yaml` is gitignored. Credentials live in the OS keychain, referenced by
  `secret_ref` — no password column, nothing in extension storage, nothing in the repository, no
  credentials in `events` payloads.
- Gmail access is read-only.
- Crawling touches public endpoints only, one request per second per host, with an honest
  User-Agent. No headless logins, ever.
