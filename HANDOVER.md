# Handover

For whoever picks this up next, human or model. Read this first, then
[`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) for the system and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for why each decision went the way it did.

Written 30 September 2026, at commit `d9acf45`, 25 commits in.

---

## 1. Orientation in sixty seconds

A job application system for Canadian AI / tech / Python roles, run locally by one person. It
captures postings, scores them, tailors a resume per posting from a verified fact bank,
assist-fills the application form, and moves each application through its states by reading the
owner's inbox. **It never submits an application.**

Three moving parts: a FastAPI service on `127.0.0.1:8765` that owns the SQLite database and all
the logic; a Chrome MV3 extension that reads pages and fills forms but decides nothing; and CLI
workers for discovery, inbox triage and the Sheets mirror.

```
api/        the brain: database, autofill rules, every endpoint
extension/  Chrome MV3: capture, card stars, autofill, claude.ai bridge, side panel
resume/     fact bank schema, selection, Typst → PDF, DOCX, the verifier
tailor/     JD analysis, scoring, shape classification, rephrasing, chat bridge
discover/   ATS detection and the nightly board crawl
inbox/      Gmail triage, matching, digest, instant push
sheets/     the Google Sheets mirror
scripts/    update.sh, autoupdate.sh, install-autoupdate.sh
docs/       SETUP, HOW-IT-WORKS, ARCHITECTURE, ROADMAP
```

## 2. State

**Working and tested.** Phases 0 through 5, plus card capture and chat tailoring.
253 Python tests, 60 extension tests, all passing on a clean checkout.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # 253 passed
cd extension && npm install && npm test  # 60 passed
```

The suite needs no `profile.yaml`: `tests/conftest.py` points every default profile lookup at
the committed `profile.example.yaml`. If you find yourself needing real personal data to make a
test pass, you have taken a wrong turn.

**Deliberately not built.** Bulk scraping of LinkedIn and Indeed listings. No jobs API exists,
both block it, both prohibit it, and the owner's account is what pays. The per-card ☆ Save and
the ATS crawl cover the same ground. Do not build it because a prompt sounds like it wants it —
ask.

**Known incomplete.** Job Bank (`jobbank.gc.ca`) is unwired; its feed format needs confirming
from a machine with network access to it. Phase 6 in the roadmap — Workday full-flow, Taleo,
iCIMS, a field-mapping learning loop, response-rate analytics — is untouched.

**Blocked on the owner, not on code.** Workday tenant URLs cannot be derived from a company
name and must be pasted in once per employer. `discover/detected.yaml` does not exist yet
because `python -m discover.detect` needs network access to the job-board hosts. The
`ai_engineer` shape has only four supporting facts, which is why tailored resumes for it are
thin; the depth check has been saying so since Phase 0.

## 3. Invariants — do not break these

These are the load-bearing rules. Each exists because the alternative causes a specific,
expensive failure.

1. **Nothing reaches a resume without a `fact_id`.** `resume/verify.py` enforces it and
   `rephrase.accept_or_discard` is the single gate every rewrite passes through, whether it came
   from the API or was scraped out of a chat window. If you add a third route to rewritten text,
   route it through that function. A resume that is merely untailored is fine; one that is
   subtly false loses the job in the interview.

2. **No number that its source fact does not contain.** Part of the same verifier and worth
   stating separately, because it is the failure that looks most plausible: a model writing
   "reduced latency 87%" about work that never measured latency.

3. **The API never submits an application.** It fills, outlines, and stops.

4. **Status only moves forward.** `inbox/sync.py:RANK`. A stray acknowledgement must not undo an
   interview.

5. **Every write is idempotent.** Capture dedupes on `dedupe_key`; the Gmail sweep keys on
   `gmail_message_id`; notifications stamp `notified_at`. Re-running any worker must be safe.

6. **A notification is stamped after the transport returns, never before.** Stamping first loses
   the push permanently when the network is down, because the message's own idempotency key
   means triage never looks at it again.

7. **`events` is append-only.** It is the answer to "why does it think that".

8. **Credentials stay in the OS keychain.** No password column, nothing in extension storage,
   nothing in the repo, no credentials in `events` payloads.

9. **The extension decides nothing.** Rules live in `api/autofill.py` so they are testable
   without a browser. A new ATS should need new selectors, not new logic.

10. **Location is out of `dedupe_key`.** A multi-city requisition is one job with several
    locations.

## 4. Traps this project has already fallen into

Each of these cost real time. They are written down so they are not rediscovered.

**Matching sites by CSS class name.** The first version of `extension/content/cards.js` found
job cards by LinkedIn's class names. It found nothing on a real page — the names had already
moved — and the tests passed because the test markup had been written to match the selectors.
Cards are now found by the job **link** (`/jobs/view/` on LinkedIn, `jk=` on Indeed), which is
the product's own routing and does not get renamed, and the card boundary is derived by climbing
until a parent holds more than one link. The tests now use markup with every class replaced by
nonsense. **If you touch a site integration, test it against markup whose class names are
wrong.**

**`innerText` in a content script.** It depends on layout — slow in a browser, and absent in
jsdom, where it silently returned `"Sun LifeToronto, ON"` as one line. Build text from the
element tree instead.

**Tests that depend on gitignored personal data.** Ten tests read `profile.yaml`, so they passed
only on the author's machine. Fixed by `default_profile_path()` and an autouse fixture. Verified
by planting a deliberately invalid `profile.yaml` and confirming all tests still pass.

**Colour written to a non-terminal.** `scripts/update.sh` emitted ANSI codes unconditionally, so
the macOS notification arrived as `^[[31mYou have uncommitted changes`. Guard on `[ -t 1 ]`.

**Inline `#` comments in a paste block.** zsh, the default macOS shell, does not honour `#`
interactively. `cp a b  # note` runs `cp a b note`. Keep comments out of copy-paste blocks in
the docs.

**A second clone nested inside the first.** The owner ran `git clone` from inside the existing
checkout. If a path looks wrong, check `pwd` before suggesting a clone; the fix is
`scripts/update.sh`, not another clone.

## 5. The fragile edges

**`extension/content/chat.js`** drives claude.ai, which is someone else's single-page app whose
markup is not a contract. It uses several selector strategies per target, judges completion by
the streaming flag *and* the text going quiet, and on any failure returns the prompt so the side
panel can offer a manual paste. When it breaks, fix the selectors — do not remove the manual
fallback, and do not weaken the validation to make a malformed reply parse.

**`extension/content/cards.js`** has the same exposure to LinkedIn and Indeed redesigns, now
mitigated as described above.

**Nobody has verified either against the live sites from this side.** Both were developed in a
Linux container with no network access to LinkedIn, Indeed or claude.ai. The logic is tested;
the selectors are educated guesses with fallbacks. Treat a bug report about them as likely real.

## 6. Working on this

- **Branch:** `claude/elegant-gauss-sn8cwh`. Push there.
- **Style:** match the surrounding code. Comments explain *why*, not *what*, and the codebase
  leans toward naming the specific failure a piece of code prevents.
- **Tests:** a test that cannot fail is worse than no test. When fixing a bug, confirm the new
  test fails against the old code before you accept it.
- **The owner is not a Python developer.** Command sequences need to be exact,
  copy-pasteable and free of inline comments. Say plainly when something cannot be done.
- **No Alembic yet.** `api/db.py:_add_missing_columns` applies additive columns on startup.
  Adding a column means adding it to `_ADDED_COLUMNS` too, or existing databases break.

## 7. If you are asked to build next

In the order that would help most:

1. **Fill the `ai_engineer` fact bank.** Not code. Four bullets is why the tailored resumes are
   thin, and no amount of engineering fixes it.
2. **Wire Job Bank** once its feed format can be confirmed.
3. **Run `discover.detect`** on a networked machine and commit `discover/detected.yaml`. Until
   then all 50 watchlist companies have no `ats_type` and the nightly crawl reads nothing.
4. **Harden the claude.ai and LinkedIn selectors** against what actually breaks in use.
5. **Phase 6** — as the roadmap lays it out, once the thing is in daily use.

Before any of it: check whether the owner has filled in `profile.yaml`. Almost everything
downstream is shaped by that file, and it was still the shipped template as of this handover.
