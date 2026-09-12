# latest-job-scraper

Calls job-board APIs daily and writes the results into a Google Sheet.
**Greenhouse** is the first (and currently only) board.

Every fetched posting is classified into a role category (see "How
classification works" below) and written to that category's own tab in the
sheet; postings matching no category (HR, Legal, Sales, Recruiting, ...) are
dropped as noise rather than written anywhere. Each row also carries a
best-effort `seniority` level (Intern, Junior, Senior, Manager, ...) read
off the title — see "How seniority tagging works" below.

Each posting is tracked by `(board, job_id)`: a run appends a row the first
time it sees a posting, never re-appends it on later days it's still open,
and removes its row once the posting is no longer in that board's feed at
all (i.e. actually closed) — so the sheet reflects what's currently posted,
not an ever-growing daily log. `scraped_date` is left as "first seen" for
you to judge how stale a still-open listing is (some firms auto-reject on a
rolling basis well before taking a listing down, which isn't something the
Greenhouse API exposes either way).

The `board` column shows the readable `firm` name from `greenhouse_boards.toml`
(e.g. "HRT", "Optiver"), not the raw Greenhouse career-page slug (e.g.
"wehrtyou", "optiverneurips") used to hit the API — see `job_boards/greenhouse/client.py`.
Because `firm` (not the token) is half of the `(board, job_id)` dedup/prune
key, renaming a firm in `greenhouse_boards.toml` makes every already-tracked
job under that entry look new again on the next run; if you ever do that,
also rewrite the `board` column of already-written rows to match, the same
way this project's own migration to firm names did (bulk `update()` per tab,
not `append`/`clear`).

## How classification works

`jobscraper/classifications/classify_roles.py` holds `CATEGORIES`: an ordered
list of `(sheet tab name, keywords)`. `classify_job` matches a job's title
ONLY (not department -- see below) against each category's keywords in order
(case-insensitive, whole-word/whole-phrase — a plain substring check would
let e.g. "data engineer" match inside "Data Engineering Recruiter") and
returns the first category that matches, or `None` if nothing does. `main.py`
groups all fetched jobs by category, drops the `None` bucket, and writes each
category to its own tab — see the module docstring for exactly why order
matters (a few categories deliberately share a broad keyword like
"developer" or "quant", so which one is checked first decides the tie-break).

Title only, deliberately: a department field can be a broad org-unit label
rather than a specific function -- on one real board, a "Business
Development Manager" posting and a "Business Manager" posting both sat under
a department literally named "Quant Management". Matching department too
routed both into QT/QR despite neither being a trading/research role, so
department is excluded from role classification entirely (seniority tagging,
below, still uses it -- that false-positive mode doesn't apply there).

Today's categories (each needs a same-named tab in the spreadsheet, already
set up): `SWE/DE`, `DA/BI`, `DS/ML/Algos`, `QT/QR`, `SRE/DevOps`. Deliberately
excluded as out of scope, not an oversight: IT/network/security/datacenter
ops roles, and hardware/silicon design roles — both fall through to `None`
today.

To extend:
- **New keyword for an existing category** — add the string to that
  category's tuple in `classify_roles.py`. That's it.
- **New category** — add a `(name, keywords)` tuple to `CATEGORIES`, then
  create a tab with that exact name (and the usual header row, see below)
  in the spreadsheet. `main.py` discovers categories from `CATEGORIES`
  alone, so no other code changes are needed.

## How seniority tagging works

`jobscraper/classifications/classify_seniority.py` holds `LEVELS`: the same
ordered `(name, keywords)` shape as `CATEGORIES`, but for seniority rather
than role, and matched against `{title} {department}` (department is fine to
include here -- a department like "Internship Program" is a much stronger,
narrower signal than the broad org-unit labels that make it unsafe for role
classification above). It runs once, at fetch time, in
`job_boards/greenhouse/client.py`, and the result is stored on `Job.seniority`
-- so by the time `main.py` runs, every job already has its level, and it
just rides along as the sheet's last column.

Levels: `Intern`, `Graduate`, `Junior`, `Mid`, `Senior`, `Lead`, `Staff`,
`Principal`, `Manager`, `Director/VP/Head`, or `Unspecified`. `Unspecified`
is the default for a title with no seniority wording at all, and it's
common — across this project's real board data, roughly 70% of postings
that pass role classification carry no seniority marker in the title.
That's not a detection gap to fix: some firms (trading/quant desks
especially) post one generic req and band the successful candidate's level
only after interviewing, so there is genuinely no signal in the title to
read. `Unspecified` is left as its own explicit value rather than defaulting
to `Mid`, since guessing "probably mid-level" for those postings would
fabricate a signal that isn't there.

To extend: same as classification above — add a keyword to an existing tier,
or a new `(name, keywords)` tuple to `LEVELS`. No spreadsheet change needed
since `seniority` is already a header column in every tab.

## How sorting works

`sheets/writer.sort_rows` runs after every append/prune (see below) and
keeps each category's tab ordered by, in priority order: `scraped_date`
(most recent first), then `location`, then `board` (firm name) — both
alphabetically. It's a no-op (no rewrite) if the tab is already in that
order, which is normal on most runs: `prune_closed_postings` only ever
removes rows, and removing rows from an already-sorted list can't un-sort
it, so a rewrite is only actually needed on a run where `append_jobs` added
something (new rows land at the bottom, unsorted relative to what's there).

`scraped_date` is stored as an ISO `YYYY-MM-DD` string, which happens to
sort correctly as plain text in calendar order, so "most recent first" is
just a reverse-alphabetical sort on that column — no date parsing needed.

To change the sort order or add another key, edit `sort_rows` directly: it's
three separate stable sorts, least-significant key first (board, then
location, then scraped_date last) — Python's `sort()` is guaranteed stable,
so that composes into the 3-level order described above without needing a
combined sort key.

## How dedup and pruning work

This is the core loop in `main.py`, per run:

1. Fetch every configured board → `all_jobs`.
2. Classify each job (above); jobs matching no category are dropped.
3. Per category: `append_jobs` reads that category's tab's existing
   `(board, job_id)` pairs, and appends only the jobs not already there. A
   posting that's still open on day 30 is simply left alone — its original
   row, and original `scraped_date`, stand untouched.
4. Per category: `prune_closed_postings` -- for every board that was just
   fetched, any *existing* row in that category's tab whose `job_id` didn't
   come back in today's fetch (for that category) gets deleted — Greenhouse
   no longer lists it, so it's closed. A board isn't considered for pruning
   at all unless it's in `fetched_boards` (in practice, every board in
   `greenhouse_boards.toml` — see the retries/backoff caveat below).
5. Per category: `sort_rows` (see "How sorting works" below) keeps the tab
   ordered: most recent first, then location, then firm.

Why age-based pruning (delete anything older than N days) was replaced:
"still open" is the only real "still hiring" signal the Greenhouse API
gives you, and it's a poor proxy for "worth applying to" — some firms run
rolling admissions and quietly auto-reject well before ever taking a
listing down, which no amount of pruning logic can detect either way.
Rather than guess at that, the sheet just tracks ground truth (open vs.
closed) and leaves `scraped_date` (first-seen date) as the input for you
to apply your own judgment on staleness.

Two edge cases worth knowing if you're reading `sheets/writer.py`:
- A board that fails to fetch (or gets removed from
  `greenhouse_boards.toml`) must **not** be passed in `fetched_boards`, or
  every one of its existing rows would look closed and get deleted. Today
  this is moot in practice, since one board's fetch failure aborts the
  whole run before pruning ever runs (see retries/backoff below) — so
  `main.py` currently just passes every configured board.
- A board that's fetched successfully but currently has **zero** open
  postings is still fully pruned — its rows correctly disappear, since an
  empty result is a legitimate "this board's listings are all closed" state,
  not a sign the fetch didn't happen.

## What's built vs. what's left for you

Built (working end-to-end):
- Greenhouse Job Board API client
  (`src/jobscraper/job_boards/greenhouse/client.py`)
- Keyword-based role classification into per-category sheet tabs, dropping
  everything else as noise
  (`src/jobscraper/classifications/classify_roles.py`)
- Keyword-based seniority tagging (Intern/Junior/Senior/Manager/...) on every
  row (`src/jobscraper/classifications/classify_seniority.py`)
- Google Sheets append-if-new + prune-closed-postings, per category
  (`src/jobscraper/sheets/writer.py`)
- Config/env var loading, orchestration (`config.py`, `main.py`)
- Daily GitHub Actions cron (`.github/workflows/daily-scrape.yml`)

Deliberately **not** built — the natural next things to add yourself:
- More job boards (Lever, Workday, ...) — add a sibling package under
  `src/jobscraper/job_boards/`, expose `fetch_jobs(token) -> list[Job]`,
  wire it into `main.py`'s loop.
- Retries/backoff — a single HTTP error currently aborts the whole run.
  (Note: `prune_closed_postings` currently assumes every configured board
  succeeded this run — see its docstring — so per-board error handling
  needs to fix that assumption too, or a board that fails without raising
  would look fully closed.)
- Notifications (Slack/email/a GitHub Actions job summary) on success or
  failure.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `.env`:

1. **Greenhouse board tokens** — configured separately in
   `greenhouse_boards.toml` at the repo root, not in `.env` (see "Adding a
   board" below).
2. **Google Sheets access** — in Google Cloud Console:
   - Enable the **Google Sheets API** for a project.
   - Create a **service account**, then a JSON key for it (download the
     `.json` file — never commit it).
   - Open your target Google Sheet → **Share** → paste the service
     account's `client_email` (looks like
     `...@<project-id>.iam.gserviceaccount.com`) → give it **Editor**
     access. Writes fail with a permission error until this is done.
   - Collapse the downloaded JSON to one line and paste it as
     `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env`:
     `python -c "import json;print(json.dumps(json.load(open('key.json'))))"`
3. **Sheet ID** — the id segment in the sheet's URL
   (`https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit`).
4. **Worksheet/tabs** — one tab per category in
   `jobscraper/classifications/classify_roles.py`'s `CATEGORIES` (currently
   `SWE/DE`, `DA/BI`, `DS/ML/Algos`, `QT/QR`, `SRE/DevOps`), named exactly
   that, each with a header row exactly matching `jobscraper.models.FIELDS`:
   `board, job_id, title, department, location, url, updated_at, scraped_date, seniority`
   This project does not create tabs (or missing header columns) for you.

## Running locally

```bash
uv run python -m jobscraper.main
```

## Running tests

```bash
uv run pytest
```

Tests cover Greenhouse JSON→`Job` parsing, keyword classification/tie-breaks,
and the sheet writer's append/prune logic against fakes — no real network or
Google credentials needed.

## Adding a board

Append a `[[boards]]` entry to `greenhouse_boards.toml` at the repo root:

```toml
[[boards]]
token = "stripe"
firm = "Stripe"
```

`token` is the slug from the company's Greenhouse careers URL
(`boards.greenhouse.io/<token>`) — that's the only field the code reads;
`firm` is just a label for your own reference. Commit and push — no other
config changes needed, since this file (unlike `.env`) is checked into the
repo and read directly by both local runs and CI.

## Cron / CI

`.github/workflows/daily-scrape.yml` runs at `0 4 * * *` UTC (12:00
Singapore time, UTC+8, no DST), plus a manual `workflow_dispatch` trigger for
testing. It needs two repository secrets:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

GitHub Actions only picks up workflows from a repository's own root
`.github/workflows/` — this project needs to live in its own git repo (not
nested inside a parent monorepo) for the schedule to fire.
