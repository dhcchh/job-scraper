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
  every configured Greenhouse board, classifies postings into ten tabs (five
  categories × `PAC`/`RoW`), appends jobs first seen this run, then removes
  jobs confirmed closed.
- `config.py` is the only module that reads environment variables and
  `greenhouse_boards.toml`; import `jobscraper.config` rather than copying its
  settings at import time. Board tokens are checked in and are not secrets.
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
- Build locations from Greenhouse's `offices[]`, normalize each office, remove
  duplicates, then join with `" | "`. Do not split `location.name` by commas:
  it is free text. `normalize_location.py` intentionally falls back to a
  cleaned unknown value rather than guessing. Add a canonical mapping before
  treating a new city as PAC. It also removes `Remote -`, `(Remote)`, `Office`,
  and Stripe-style `Location(s)` wrappers before lookup.
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
- **Ashby job board support**: add a second board type under
  `job_boards/ashby/client.py` with its own `fetch_jobs() -> list[Job]`.
  Ashby's public API serves jobs at
  `https://api.ashbyhq.com/posting-api/job-board/<board_slug>`, returning
  JSON similar in shape to Greenhouse. Needs a new `[[ashby_boards]]` section
  in a config file (or extend `greenhouse_boards.toml` with a `source` field)
  and a loop in `main.py` that fetches Ashby boards alongside Greenhouse.
  Firms to add: OpenAI (`openai`), Notion (`notion`), Airwallex
  (`airwallex`), Snowflake (`snowflake`), Exa (`exa`), Sierra (`sierra`),
  BlockTech (`blocktech`) — all confirmed live on `jobs.ashbyhq.com`.
- **Lever Job Board Support** Check the lever job portal, Spotify, Palantir, ShopBack, prestolabs, CSIT  is confirmed
- If more than one person operates the production sheet, add a distributed
  lock; GitHub Actions concurrency cannot prevent an overlapping local run.

## Scale

The current sequential fetcher and Google Sheet datastore fit a small-to-
medium board list. As history and board count grow, full-sheet reads, API
retries/rate limits, and Sheets' cell cap become the limiting factors. For a
much larger deployment, move to concurrent/batched fetching, a database, and
a dedicated scheduler.
