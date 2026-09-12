# latest-job-scraper

Calls job-board APIs daily and writes the results into a Google Sheet.
**Greenhouse** is the first (and currently only) board.

Each run appends that day's jobs as new rows, then prunes any row whose
`scraped_date` is older than `PRUNE_AFTER_DAYS` (default 60) — so the sheet
holds a rolling window of recent history, not an ever-growing archive and not
just a snapshot of today.

## What's built vs. what's left for you

Built (working end-to-end):
- Greenhouse Job Board API client (`src/jobscraper/greenhouse/client.py`)
- Google Sheets append + prune-by-date (`src/jobscraper/sheets/writer.py`)
- Config/env var loading, orchestration (`config.py`, `main.py`)
- Daily GitHub Actions cron (`.github/workflows/daily-scrape.yml`)

Deliberately **not** built — the natural next things to add yourself:
- More job boards (Lever, Workday, ...) — add a sibling package under
  `src/jobscraper/`, expose `fetch_jobs(token) -> list[Job]`, wire it into
  `main.py`'s loop.
- Filtering by title/department/keyword before appending.
- Cross-run dedup (right now every still-open posting gets a fresh row every
  day it's fetched).
- Retries/backoff — a single HTTP error currently aborts the whole run.
- Notifications (Slack/email/a GitHub Actions job summary) on success or
  failure.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `.env`:

1. **Greenhouse board tokens** — the slug in a company's careers URL
   (`boards.greenhouse.io/<token>`). Public API, no key needed.
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

## Adding a board token

Add it to the comma-separated `GREENHOUSE_BOARD_TOKENS` (locally in `.env`,
in CI as the `GREENHOUSE_BOARD_TOKENS` GitHub secret).

## Cron / CI

`.github/workflows/daily-scrape.yml` runs at `0 4 * * *` UTC (12:00
Singapore time, UTC+8, no DST), plus a manual `workflow_dispatch` trigger for
testing. It needs three repository secrets:

- `GREENHOUSE_BOARD_TOKENS`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

GitHub Actions only picks up workflows from a repository's own root
`.github/workflows/` — this project needs to live in its own git repo (not
nested inside a parent monorepo) for the schedule to fire.
