# autoJobApply-Tracker

A semi-automated job application system for **Canadian AI, tech and Python roles**: discover
roles, tailor a resume to each one, assist-fill the application form, and track every
outcome — with email triage feeding status back in automatically.

**Setting it up?** Follow [`docs/SETUP.md`](docs/SETUP.md) — one ordered pass, start to finish.
For why it is built this way, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); for what was
built when, [`docs/ROADMAP.md`](docs/ROADMAP.md).

## The one-line version

You browse or the crawler finds jobs → the system scores fit and tailors a resume from a
verified fact bank → the extension fills the application form and you press submit →
Gmail triage watches for replies and moves the application through its states → you get one
digest a day and an instant ping for anything time-sensitive.

## Phase 0 is built: the resume pipeline

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

cp profile.example.yaml profile.yaml      # then replace every value with your own

# How deep is the fact bank for each role shape?
.venv/bin/python -m resume.render --profile profile.yaml --depth-only

# Render one shape, ATS-safe, PDF + DOCX
.venv/bin/python -m resume.render --profile profile.yaml \
    --shape ai_engineer --out out/resume.pdf --docx out/resume.docx

# Maximum parser safety: dates inline instead of at the right margin
.venv/bin/python -m resume.render --profile profile.yaml \
    --shape ai_engineer --out out/resume.pdf --date-style inline

.venv/bin/python -m pytest
```

Shapes are `ai_engineer`, `backend_python` and `fullstack`. All three live in the fact bank;
each rendered resume targets exactly one, with its own headline and summary.

The command exits non-zero and writes nothing usable if any bullet fails to trace back to a
fact, if a number appears that its source fact does not contain, if a skill reaches the page
unsupported by a selected bullet, or if the text extracted back out of the finished PDF does
not match what was rendered. That last check is not theoretical: right-aligned employment dates
are extracted *in the middle of the first bullet* by two of three tested extraction heuristics,
so a bullet can reach an employer as "…with an online quote wizard and photo **Mar 2026 –
Present** uploads." The layout is kept because it is the established format and a layout-aware
parser reads it correctly; `--date-style inline` removes the risk for anyone who would rather
not take it.

## Phase 1 is built: capture and track

Start the tracker, then load the extension:

```bash
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --reload
```

In Chrome: `chrome://extensions` → Developer mode → **Load unpacked** → pick `extension/`.
There is no build step. Click the toolbar icon for the queue, or press **Ctrl+Shift+S**
(**Cmd+Shift+S** on a Mac) on any job posting to save it.

What the capture does, in order: read the page's schema.org `JobPosting` JSON-LD, fill any
gaps from a site adapter (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable,
LinkedIn, Indeed), then fall back to Open Graph and `<title>` so an unknown careers page
still captures. It only reads the page you are already looking at, in your own session —
no crawling and no logins.

Dedupe is the part that matters. `Sr. ML Eng @ Cohere Inc.` in Toronto and
`Senior Machine Learning Engineer @ cohere` in Vancouver are **one job with two locations**,
not two rows. And if you already applied, re-capturing warns you instead of letting you
apply twice.

```bash
.venv/bin/python -m pytest          # 86 backend tests
npm test --prefix extension         # 12 capture tests against real DOMs
```

## Phase 2 is built: assisted apply

With the tracker running and the extension loaded, open a posting's application form and press
**Fill this form** in the side panel. It fills every field it can, attaches the tailored resume
for the shape you picked, and outlines what it touched — green for filled, amber for corrected,
yellow for "your turn".

**It never clicks submit.** You review and submit. When the confirmation page appears, the
extension recognises it and moves the application to `applied` on its own, which is why the
tracker stays honest without anyone remembering to update it.

The matching logic lives in `api/autofill.py`, not in the extension: the content script
enumerates the fields it can see, the API decides what belongs in each one. A new ATS then needs
new *selectors*, not new rules, and every rule is testable without a browser.

Four things it will not do:

- **Generate motivation text.** "Why this company" comes back as a skip. A generated answer to
  that question is visibly generated.
- **Autofill a password.** Credentials are handled only through the keychain-backed vault below.
- **Guess.** An unmatched label is reported as unmatched. One wrong value teaches you to stop
  trusting the whole fill, which costs more than a blank field.
- **Disclose anything voluntary** unless `self_id.mode` is `disclose`, and then only by matching
  the options the page itself offers.

### Workday

Workday gets the most attention because it holds the most Canadian enterprise hiring, and
because it inverts the usual problem: it parses your resume and fills the form itself, usually
getting something wrong. So the adapter diffs its parse against `profile.yaml` and corrects the
differences — corrections are counted and outlined separately from fresh fills.

Per-tenant accounts are the other half. Every employer is its own Workday login, which is the
worst friction in the whole process:

```bash
curl -X POST http://127.0.0.1:8765/ats-accounts -H 'Content-Type: application/json' \
  -d '{"tenant":"rbc","username":"you@example.com","password":"…","company":"RBC"}'
```

The password goes to the OS keychain; the database stores only a reference to it. If no keychain
is available the call fails rather than quietly writing a plaintext fallback.

## Phase 3 is built: scoring and tailoring

```bash
python -m tailor.run --jd posting.txt                       # score only
python -m tailor.run --jd posting.txt --tailor --out out/x.pdf
python -m tailor.run --jd posting.txt --tailor --rephrase --out out/x.pdf
```

Or through the API, on a job the extension already captured:
`POST /jobs/{id}/score` and `POST /jobs/{id}/tailor`.

**The gate runs before anything expensive.** It reads the posting for the technologies it
actually names, classifies it into one of the three shapes, compares it against what the fact
bank can support, and penalises a seniority gap that a keyword score would otherwise miss
entirely. Below the threshold the application is marked `skipped` **with the reason recorded**,
so a posting that reappears next week is not relitigated from scratch.

Everything up to this point is deterministic and needs no API key: vocabulary extraction, the
required/nice-to-have split, shape classification, scoring, and the gap report. JD analysis is
cached by `description_hash`, because the same posting arrives from LinkedIn, the company board
and the nightly crawl.

**`--rephrase` is the only step that calls a model.** It may reword a bullet that is already on
the page to use the posting's vocabulary. It may not add a bullet, drop one, change a number or
claim a skill — and that is not enforced by asking nicely. The response is re-validated by the
same traceability checks from Phase 0, and anything that fails is discarded in favour of the
original wording. A resume that is merely untailored is fine; one that is subtly false is not.
No credential, an API error, or a response that does not validate all land in the same place:
the original text, unchanged.

Install the optional dependency only if you want that step:

```bash
.venv/bin/pip install "anthropic>=0.40"   # then export ANTHROPIC_API_KEY
```

**Gaps are a skip signal and a learning list.** They are never an instruction to claim the
missing thing.

## Phase 4 is built: email triage

```bash
.venv/bin/pip install ".[gmail]"          # then run once to complete OAuth consent
python -m inbox.run --sync --ghost --digest
```

A rejection email moves the row to `rejected` without you touching it. An assessment with a
72-hour window is surfaced immediately, because that is the single most expensive thing to
miss. Silence becomes `ghosted` after 21 days, so the pipeline shows reality rather than 200
rows of hope.

```
NEEDS YOU NOW
  • [assessment] Next step: coding challenge
    due Sun 27 Sep 08:00 — 48h left
  • [interview_invite] Interview — ML Engineer
    RBC Careers <talent@rbc.ca>

COULD NOT PLACE (tell me which application)
  • PyCoder's Weekly #640
    news@python.org — weak signal (none)
```

Classification is rule-based and deterministic. Recruiting email is unusually formulaic, so a
curated phrase list gets most of the way, costs nothing, and can be read and corrected when it
is wrong — none of which is true of a model call on every message in your inbox. Ordering is
the part that matters: nearly every rejection opens with "thank you for applying", so
acknowledgement phrases must never outrank rejection phrases.

Three rules keep it honest:

- **Quoted history is stripped**, not merely truncated. A two-line interview invite above a
  quoted rejection would otherwise be read as a rejection.
- **An application only ever moves forward.** A late "we received your application" cannot undo
  an interview, and a closed application is not reopened by a stray email.
- **Ambiguity becomes an orphan.** Two applications at one company with nothing to separate
  them are not guessed between — a misattached rejection closes the wrong one and hides a live
  application. Orphans go in the digest for you to place, once, via
  `PATCH /email-links/{id}`.

Gmail is read-only and incremental by `historyId`: a sweep reads the handful of messages that
arrived, never the whole mailbox. The Gmail-specific code sits behind one small class, so
everything that decides anything is tested against plain dictionaries.

Run it from cron once it is on a VPS:

```cron
30 7 * * * cd /srv/tracker && .venv/bin/python -m inbox.run --sync --ghost --digest
```

## Phase 5 is built: nightly discovery

```bash
python -m discover.detect                      # once: which ATS does each company use?
python -m discover.crawl --tailor-top 5        # nightly: read boards, score, tailor the best
```

Reads each watchlist company's public board, keeps the Canadian engineering roles, captures
them through the same deduplicating path the extension uses, scores everything, and renders
resumes for the best of the night. Re-running adds nothing: a job already known is counted and
skipped.

```
4/4 boards read, 8 postings, 5 filtered out, 3 new (0 already known), 3 passed the gate

Best of tonight:
  • Cohere — Senior AI Engineer (100)
  • Jobber — Full Stack Developer (100)
  • Clio — Backend Engineer (83)
```

**Two filters do the cheap work** so the gate does not have to. A title filter keeps only the
three shapes — deliberately wide, because the score gate is the real defence and a title wrongly
excluded here is a job never seen at all. A location filter keeps Canada and unqualified remote,
and drops the rest; Cohere's board lists San Francisco and London roles that are not this search.

Ambiguous city names get their own handling: **London is a real Ontario city** and a much more
famous English one, so a bare "London" on a Canadian company's board resolves to Ontario while
"London, UK" does not. Same for Sydney, Nova Scotia.

**Board readers are written to survive drift.** Every field lists the keys it might appear
under and the first present wins, so a vendor renaming `absolute_url` to `url` costs one entry
in a list rather than a 2am crash — and a posting missing its apply URL is dropped rather than
half-saved. The shapes come from the vendors' documented board APIs; **confirm them on the first
real run**, which is also the first time this machine can reach those hosts.

Ties in the nightly ranking break toward the posting with more matched requirements: two jobs at
100% coverage are not equally good bets when one named two things you have and the other named
seven.

```cron
0 2 * * * cd /srv/tracker && .venv/bin/python -m discover.crawl --tailor-top 5
30 7 * * * cd /srv/tracker && .venv/bin/python -m inbox.run --sync --ghost --digest
```

## The Google Sheets mirror

```bash
python -m sheets.run --spreadsheet 1AbC...xyz     # the id from the sheet's URL
```

```
ID | Company | Title                | Status    | Score | Shape          | Notes
---+---------+----------------------+-----------+-------+----------------+------
1  | Cohere  | Senior AI Engineer   | applied   | 100   | ai_engineer    |
2  | Clio    | Backend Engineer     | tailored  | 83    | backend_python |
3  | Jobber  | Full Stack Developer | interview | 74    | fullstack      |
```

**SQLite stays the source of truth. The sheet is a view you can edit in two columns** — Status
and Notes — and nothing else travels back. That asymmetry is the design: three writers against
a spreadsheet with no transactions and no unique constraints is exactly how this project would
have corrupted itself.

The sync is pull-then-push, and the pull has one rule that makes it safe: **a cell counts as
your edit only when it differs from what the last push wrote there.** Without that memory the
sync has two failure modes and no good one — ignore your edits, or re-apply stale cells over
the database's own progress. An application moved to `rejected` by an email on Tuesday would
be dragged back to `applied` every night by a cell nobody had touched in weeks. There is a test
for exactly that.

Everything else is defensive in the ordinary way: rows are matched by an `ID` column rather
than by position, columns are found by header text so dragging one does not corrupt the sync,
an unrecognised status is reported rather than applied, a row you typed by hand is left alone,
and every status change pulled in is recorded as an event with `source="sheet"`.

Run it after the nightly crawl:

```cron
0 2 * * *  .venv/bin/python -m discover.crawl --tailor-top 5
15 2 * * * .venv/bin/python -m sheets.run --spreadsheet $TRACKER_SPREADSHEET_ID
30 7 * * * .venv/bin/python -m inbox.run --sync --ghost --digest
```

> The mirror adds two columns to `applications`. There are no migrations yet, so if you already
> have a `data/tracker.sqlite` from an earlier run, delete it and re-capture — there is nothing
> in it worth keeping at this stage.

## Who to watch

`discover/watchlist.yaml` holds 50 Canadian employers that hire Python, AI and backend
engineers: 40 with candidate job-board tokens to probe, and 10 enterprises whose Workday
tenant has to be filled in by hand.

```bash
.venv/bin/python -m discover.detect --watchlist discover/watchlist.yaml \
    --out discover/detected.yaml
```

It asks each company's public job-board endpoint which ATS they actually use, rather than
guessing from a list that goes stale. One request per second, honest User-Agent, public
endpoints only, no logins. Needs network access to `boards-api.greenhouse.io`,
`api.lever.co`, `api.ashbyhq.com`, `apply.workable.com`, `api.smartrecruiters.com` and
`*.recruitee.com`.

## Design principles

1. **Human presses submit.** The system does everything up to the submit button. This is
   what keeps accounts un-banned and applications non-embarrassing.
2. **Never invent a qualification.** Tailoring reorders, re-weights, and rephrases facts
   that are already true. Every rendered bullet traces back to a `fact_id`.
3. **One source of truth per thing.** Resume content lives in `profile.yaml`. Application
   state lives in the database. Google Sheets and Docs are *views*, not storage.
4. **Useful on day three.** The tracker and the capture button ship before any LLM feature.
   Tracking is most of the value; tailoring is the multiplier.
