# autoJobApply-Tracker

A semi-automated job application system for **Canadian AI, tech and Python roles**: discover
roles, tailor a resume to each one, assist-fill the application form, and track every
outcome — with email triage feeding status back in automatically.

**Current state:** design phase. No code yet. Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
then [`docs/ROADMAP.md`](docs/ROADMAP.md) for the build order.

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
