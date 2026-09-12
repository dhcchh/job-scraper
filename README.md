# latest-job-scraper

Calls job-board APIs daily and writes the results into a Google Sheet.
**Greenhouse** is the first (and currently only) board.

Each posting is tracked by `(board, job_id)`: a run appends a row the first
time it sees a posting, never re-appends it on later days it's still open,
and removes its row once the posting is no longer in that board's feed at
all (i.e. actually closed) — so the sheet reflects what's currently posted,
not an ever-growing daily log. `scraped_date` is left as "first seen" for
you to judge how stale a still-open listing is (some firms auto-reject on a
rolling basis well before taking a listing down, which isn't something the
Greenhouse API exposes either way).

## How dedup and pruning work

This is the core loop in `main.py`, per run:

1. Fetch every configured board → `all_jobs`.
2. `append_jobs`: read the sheet's existing `(board, job_id)` pairs, append
   only the jobs not already there. A posting that's still open on day 30
   is simply left alone — its original row, and original `scraped_date`,
   stand untouched.
3. `prune_closed_postings`: for every board that was just fetched, any
   *existing* sheet row whose `job_id` didn't come back in today's fetch
   gets deleted — Greenhouse no longer lists it, so it's closed. A board
   isn't considered for pruning at all unless it's in `fetched_boards`
   (in practice, every board in `greenhouse_boards.toml` — see the
   retries/backoff caveat below).

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
- Greenhouse Job Board API client (`src/jobscraper/greenhouse/client.py`)
- Google Sheets append-if-new + prune-closed-postings (`src/jobscraper/sheets/writer.py`)
- Config/env var loading, orchestration (`config.py`, `main.py`)
- Daily GitHub Actions cron (`.github/workflows/daily-scrape.yml`)

Deliberately **not** built — the natural next things to add yourself:
- More job boards (Lever, Workday, ...) — add a sibling package under
  `src/jobscraper/`, expose `fetch_jobs(token) -> list[Job]`, wire it into
  `main.py`'s loop.
- Filtering by title/department/keyword before appending.
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
4. **Worksheet/tab** — must already exist, with a header row exactly
   matching `jobscraper.models.FIELDS`:
   `board, job_id, title, department, location, url, updated_at, scraped_date`

## Running locally

```bash
uv run python -m jobscraper.main
```

## Running tests

```bash
uv run pytest
```

Tests cover Greenhouse JSON→`Job` parsing and the sheet writer's
append/prune logic against fakes — no real network or Google credentials
needed.

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
