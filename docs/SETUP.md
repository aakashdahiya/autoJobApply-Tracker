# Setup

One ordered pass, start to finish. Parts 1 and 2 are worth doing in one sitting — at the end of
them the system already saves you time. Parts 3 to 5 can wait for a weekend.

Each step says what to run, what you should see, and what to do when it does not.

**You need:** Python 3.11+, Chrome, and a Google account. Total hands-on time is about an hour,
most of it in Part 4 clicking through Google's console.

---

## Part 1 — The resume pipeline (~15 minutes)

### 1. Install

```bash
git clone https://github.com/aakashdahiya/autoJobApply-Tracker.git
cd autoJobApply-Tracker
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

**Expect:** `213 passed`. If tests fail here, stop — nothing downstream will behave.

### 2. Fill in your fact bank

```bash
cp profile.example.yaml profile.yaml
```

Open `profile.yaml` and replace every value. It is gitignored, so it holds your real details
and never reaches the repository. Three things matter more than the rest:

- **`facts`** — every bullet you could ever put on a resume, written once, tagged with the
  `shapes` it genuinely supports. This is the only legitimate source of resume content; nothing
  else can reach a rendered page. Aim for fifteen or so.
- **`constraints.work_auth_expiry`** — your PGWP end date.
- **`identity.postal_code` and `street`** — application forms ask; the resume never shows them.

### 3. Prove it renders

```bash
.venv/bin/python -m resume.render --profile profile.yaml --depth-only
```

**Expect:** one line per shape saying how many facts back it. A `WARN` is information, not an
error — it means that shape is too thin to tailor for credibly, and the fix is another project
or another bullet, never a lower threshold.

```bash
.venv/bin/python -m resume.render --profile profile.yaml \
    --shape ai_engineer --out out/resume.pdf --docx out/resume.docx
```

**Expect:** `traceability ok, PDF text layer verified`. Open the PDF. If it renders in a fallback
font, install Calibri or Carlito — the template asks for them in that order.

**If it fails:** the error names the bullet and the rule it broke. A traceability failure is the
system refusing to put something on a page it cannot trace back to a fact, which is the point.

---

## Part 2 — Capture and apply (~20 minutes)

### 4. Start the tracker

```bash
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8765 --reload
```

Leave it running. **Expect:** `curl http://127.0.0.1:8765/health` returns `{"ok":true}`.

> Bind to `127.0.0.1` only. There is no authentication yet, and the API hands out your profile
> data and, on request, stored ATS passwords.

### 5. Load the extension

`chrome://extensions` → turn on **Developer mode** → **Load unpacked** → select the `extension/`
folder. There is no build step.

**Expect:** a toolbar icon. Click it for the side panel; it should say `0 tracked` rather than
"Tracker unreachable".

### 6. Save a job, then fill a form

Open any job posting and press **Ctrl+Shift+S** (**Cmd+Shift+S** on a Mac).

**Expect:** a "Saved" notification and a row in the side panel.

**If it says "Could not read this posting":** you are probably on a search results page rather
than the job's own page. Open the posting itself.

Now open that job's application form, pick a resume shape in the panel, and press
**Fill this form**. Green outlines are filled, amber are corrected, yellow are yours to answer.
**Review everything, then submit it yourself** — the extension never clicks submit. When the
confirmation page appears it marks the application `applied` on its own.

### 7. Score and tailor against a real posting

```bash
pbpaste > /tmp/jd.txt     # or paste the job description into a file by hand
.venv/bin/python -m tailor.run --jd /tmp/jd.txt --tailor --out out/tailored.pdf
```

**Expect:** a score, the shape it picked, matched terms, and the gaps. Gaps are a skip signal
and a learning list — never a prompt to claim the missing thing.

---

## Part 3 — Discovery (~20 minutes, needs unrestricted network)

### 8. Work out which ATS each company uses

```bash
.venv/bin/python -m discover.detect
```

Takes about four minutes at one request per second. **Expect:** a line per company and a
summary like `31/40 resolved: {'greenhouse': 18, 'lever': 7, 'ashby': 6}`. It writes
`discover/detected.yaml`.

Unresolved companies are normal — the candidate slug was wrong, or they use an ATS with no
public board. Edit their `slugs` in `discover/watchlist.yaml` and re-run for just that company:
`--only cohere`.

### 9. Add the Workday tenants by hand

The ten enterprises in the watchlist (RBC, TD, Scotiabank, BMO, CIBC, Telus, Bell, Sun Life,
Manulife, OMERS) each run their own Workday tenant, and **a tenant URL cannot be derived from a
company name**. Find each careers site, copy the URL, and paste the tenant into
`discover/watchlist.yaml`:

```yaml
  - {name: RBC, city: Toronto, workday_tenant: "rbc.wd3.myworkdayjobs.com/rbc-careers"}
```

Once per employer, ever. This is the one part of the watchlist nobody can automate.

### 10. First crawl — and confirm the board shapes

```bash
.venv/bin/python -m discover.crawl --per-board 3
```

**Read the output carefully — this is the one verification step that matters.** The board
readers were written from the vendors' documented APIs but have never seen a live response.

**Expect:** real job titles and Canadian locations. If titles come back blank or every posting
is filtered out, a vendor uses a different field name. Open `discover/boards.py`, find that
ATS's `BoardSpec`, and add the real key to the relevant tuple — the fields are lists precisely
so this is a one-line fix.

Then run it properly:

```bash
.venv/bin/python -m discover.crawl --tailor-top 5
```

---

## Part 4 — Google (~20 minutes)

Both the inbox sweep and the sheet mirror need Google OAuth. You do the console part once and
the consent twice, because they use different scopes and separate token files — the mirror
writes one spreadsheet, the inbox sweep only reads mail.

### 11. Create OAuth credentials

In the Google Cloud console: create a project, enable the **Gmail API** and the **Google Sheets
API**, then create an **OAuth client ID** of type **Desktop app** and download its JSON. Save it
as `credentials.json` in the repository root (it is gitignored).

While the consent screen is in testing mode, add your own address under test users, or the
consent will be refused.

> Google moves these screens around. The three things you need are constant: both APIs enabled,
> a Desktop-app OAuth client, and its JSON on disk as `credentials.json`.

### 12. Connect Gmail

```bash
.venv/bin/pip install -e ".[gmail]"
.venv/bin/python -m inbox.run --sync --digest
```

A browser opens for consent. **Expect:** `Synced N messages: …` then the digest. The token is
saved to `data/gmail_token.json`; later runs will not prompt.

The first sweep reads your recent mail and will produce orphans — messages it could not
confidently attach to an application. That is deliberate: a misattached rejection closes the
wrong application and hides a live one. Place them once:

```bash
curl -s "http://127.0.0.1:8765/email-links?orphans_only=true" | python3 -m json.tool
curl -X PATCH http://127.0.0.1:8765/email-links/3 \
     -H 'Content-Type: application/json' \
     -d '{"application_id": 12, "apply_status": true, "reviewed": true}'
```

Later messages in that thread inherit the match, so it is genuinely once per conversation.

### 13. Connect the sheet

Create a blank Google Sheet. Copy the id out of its URL —
`docs.google.com/spreadsheets/d/`**`THIS PART`**`/edit`.

```bash
export TRACKER_SPREADSHEET_ID=1AbC...xyz
.venv/bin/python -m sheets.run --spreadsheet "$TRACKER_SPREADSHEET_ID"
```

**Expect:** `N rows written`, and an `Applications` tab that fills in.

**You may edit two columns: Status and Notes.** Everything else is overwritten on the next push.
Edit anything else and it reverts — that is the mirror working, not a bug.

---

## Part 5 — Leave it running

Localhost is fine while you are building. Once you want the nightly crawl and the morning
digest to happen whether or not your laptop is awake, move the whole thing to a small VPS —
and add authentication to the API as part of that move, not after it.

```cron
0  2 * * * cd /srv/tracker && .venv/bin/python -m discover.crawl --tailor-top 5
15 2 * * * cd /srv/tracker && .venv/bin/python -m sheets.run --spreadsheet $TRACKER_SPREADSHEET_ID
30 7 * * * cd /srv/tracker && .venv/bin/python -m inbox.run --sync --ghost --digest
```

---

## What a normal day looks like

Morning: read the digest. It leads with whatever is time-critical — an assessment with a
72-hour window, an interview to book — then what is apply-ready, what moved by email overnight,
what is going quiet, and anything it could not place.

Then, for each apply-ready job: open it from the side panel, **Fill this form**, review, submit.
About thirty seconds each. Rejections file themselves. Silence becomes `ghosted` after 21 days,
so the pipeline shows what is actually alive.

Once a week, check the depth report. If the shape you most want to be hired for is the thinnest,
that is the most useful thing the system will tell you.

---

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| Side panel says "Tracker unreachable" | The API is not running | Start uvicorn; check `curl 127.0.0.1:8765/health` |
| "Could not read this posting" | A search results page, not the posting | Open the job's own page |
| Autofill leaves fields blank | No rule matched that label | Expected — blanks are honest. Add a recurring one to `answers` in `profile.yaml` |
| Traceability check fails | A bullet cannot be traced to a fact | Read the error; it names the fact and the rule |
| Crawl returns nothing | `detected.yaml` missing, or a board field renamed | Run `discover.detect`; check `--per-board 3` output |
| Every posting filtered out | Location filter, or an unusual title | Check the location strings the board returns |
| A sheet edit reverted | You edited a read-only column | Only Status and Notes travel back |
| Gmail or Sheets refuses consent | Test user not added while in testing mode | Add your address under test users |
| `pip install -e .` fails | Old checkout without the explicit package list | `git pull` |
| Old database, new columns | No migrations yet | Delete `data/tracker.sqlite` and re-capture |
