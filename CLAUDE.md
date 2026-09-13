# CLAUDE.md

## Commands

```bash
uv sync
cp .env.example .env

uv run python -m jobscraper.main              # live scrape: append new and prune closed jobs
uv run python -m jobscraper.sheets.backfill   # refresh existing rows after metadata/classifier changes

uv run pytest
uv run pytest tests/sheets/test_writer.py
```

There is no formatter or linter. Tests never use the network or live Google
credentials: `tests/conftest.py` supplies dummy config and sheet tests use a
small fake `Worksheet`.

## Architecture and invariants

- The daily GitHub Actions workflow runs `jobscraper.main`. Each run fetches
  every configured Greenhouse, Lever, and Ashby board, classifies postings
  into ten tabs (five categories × `PAC`/`RoW`), appends jobs first seen
  this run, then removes jobs confirmed closed.
- `config.py` is the only module that reads environment variables,
  `greenhouse_boards.toml`, `lever_boards.toml`, and `ashby_boards.toml`;
  import `jobscraper.config` rather than copying its settings at import
  time. Board tokens are checked in and are not secrets.
  `_load_boards` validates every `[[boards]]` entry has `token`, `firm`, and
  `company_type` at import time, raising `ValueError` naming the offending
  entry and field(s) rather than letting a typo surface as a `KeyError` deep
  in `main.py`'s fetch loop.
- `Job` in `models.py` is the shared data shape. Keep `FIELDS`,
  `Job.to_row()`, and the sheet header aligned. The row identity is
  `(board, job_id)`, not `job_id` alone, because Greenhouse IDs are tenant
  scoped. `first_published` is the best available Greenhouse "went live"
  signal; `updated_at` can change after edits and `scraped_date` is only when
  this project first saw the job.
- `greenhouse/client.py` fetches the public Job Board API with `content=true`.
  It retries transient failures three times; `main.py` logs and skips a board
  that still fails so it cannot trigger false pruning elsewhere.
- `lever/client.py` fetches the public Postings API (`?mode=json`) the same
  way, with the same retry/skip behavior. Lever's response has no edited-at
  timestamp at all, so `first_published` comes from `createdAt` and
  `updated_at` is left `""` rather than fabricated; `department` falls back
  to `categories.team` when a board (e.g. Palantir) never sets
  `categories.department`.
- `ashby/client.py` fetches the public Posting API
  (`api.ashbyhq.com/posting-api/job-board/<slug>`), same retry/skip
  behavior. Same no-edited-at-timestamp shape as Lever: `first_published`
  comes from `publishedAt`, `updated_at` stays `""`; `department` falls back
  to `team` the same way Lever's falls back to `categories.team`, though no
  live Ashby board has needed that fallback yet. Ashby splits its per-office
  list across two fields (top-level `location` plus
  `secondaryLocations[].location`), not one array like Greenhouse/Lever.
- Build locations from Greenhouse's `offices[]`, Lever's
  `categories.allLocations`, or Ashby's `location` +
  `secondaryLocations[].location`, normalize each office, remove
  duplicates, then join with `" | "`. Do not split
  `location.name`/`categories.location` by commas: it is free text.
  `normalize_location.py` intentionally falls back to a cleaned unknown
  value rather than guessing. Add a canonical mapping before treating a new
  city as PAC. It also removes `Remote -`, `(Remote)`, `Office`,
  Stripe-style `Location(s)`, a trailing 2-letter country code in parens,
  Spotify-style `(... Mix)` work-arrangement annotations, a repeated "City,
  City" wrapper, and (seeded from Ashby's Airwallex/Snowflake boards) a
  spaced-out ISO-code prefix like `"AU - Sydney"` before lookup.
- `classify_region` routes a posting to PAC if any normalized office is in a
  PAC country. `classify_roles.classify_job` returns `None` for recruiter
  titles before applying category keywords; keyword precedence is intentional.
- `sheets/writer.py` deduplicates and prunes by `(board, job_id)`. Only prune
  boards fetched successfully (and all tokens for a multi-token firm).
  Pruning uses an atomic Sheets batch of row deletions, but concurrent writers
  can still duplicate appends. Use `USER_ENTERED` values so Sheets preserves
  dates and linkifies URLs.
- Each job tab has a conditional formatting rule
  (`=AND(ISNUMBER($D2), $D2>=TODAY()-7)`) highlighting columns A–K green when
  `first_published` falls within the past 7 days. Like all sheet formatting, it
  survives append and prune runs for free.
- `sheets/backfill.py` is manual only. Run it after changing `FIELDS`,
  classification metadata, location normalization, or `first_published`.
  It refreshes cells in place but deliberately does not move rows between tabs
  or rename board values, which could lose data if interrupted.
- The scraper logs the spreadsheet title/URL and writes a GitHub Actions job
  summary. Workflow-level concurrency protects Action-triggered runs; it does
  not protect a local run pointed at the production sheet.
- `GOOGLE_SHEET_ID` is set independently in `.env` (local) and the GitHub
  Actions secret (CI); nothing else cross-checks the two. The optional
  `GOOGLE_SHEET_TITLE` env var closes that gap: when set, `get_spreadsheet()`
  raises before any board is touched if the opened spreadsheet's title
  doesn't match, rather than only being visible in the logged title/URL.

## Open work

- Recognize numeric and level-based seniority (for example, `Engineer II`,
  `SDE II`, and `L4/L5`) rather than classifying it as `Unspecified`.
- Add an optional Telegram digest for newly appended DA/BI, DS/ML/Algos, and
  SWE/DE postings. Send one batched message per successful run; it will need
  bot, chat, and thread secrets, plus access to the newly appended jobs rather
  than only their counts.
- `sheets/backfill.py` only ever re-fetches `config.GREENHOUSE_BOARDS` for
  its `location`/`first_published` overrides and its `company_type`
  refresh — this predates Lever support and still hasn't been extended to
  either Lever or Ashby, so a Lever- or Ashby-sourced row only ever gets
  `_renormalize_location`'s best-effort cleanup, never a fresh-data
  override, and never picks up a `company_type` edit made in
  `lever_boards.toml`/`ashby_boards.toml` after the row was written.
- If more than one person operates the production sheet, add a distributed
  lock; GitHub Actions concurrency cannot prevent an overlapping local run.

## Scale

The current sequential fetcher and Google Sheet datastore fit a small-to-
medium board list. As history and board count grow, full-sheet reads, API
retries/rate limits, and Sheets' cell cap become the limiting factors. For a
much larger deployment, move to concurrent/batched fetching, a database, and
a dedicated scheduler.
