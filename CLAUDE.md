# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                        # install dependencies
cp .env.example .env           # then fill in (see README's Setup section)

uv run python -m jobscraper.main   # run the scraper once, locally

uv run pytest                              # full test suite
uv run pytest tests/sheets/test_writer.py  # one test file
uv run pytest tests/sheets/test_writer.py::test_append_jobs_skips_already_tracked_job  # one test
```

No linter/formatter is configured in this repo.

Tests never hit the network or real Google credentials: `tests/conftest.py` sets
dummy `GOOGLE_SHEET_ID`/`GOOGLE_SERVICE_ACCOUNT_JSON` env vars before
`jobscraper.config` is ever imported (it reads required env vars at import
time and raises without them), and `sheets/writer.py`'s tests exercise a
tiny in-file fake `Worksheet` instead of `gspread`.

## Architecture

Single package (`src/jobscraper/`), one entrypoint (`main.py`), run once
daily by `.github/workflows/daily-scrape.yml` (`uv run python -m
jobscraper.main`). Per run: fetch every configured board → dedup-append new
postings to the sheet → prune postings that closed. Read `README.md` for
full user-facing setup/behavior; this section is the cross-file design that
isn't obvious from any single file.

**`config.py`** is the only place that reads env vars or
`greenhouse_boards.toml`, and does so once at import. Everything else reads
values through the module (`config.GOOGLE_SHEET_ID`, etc.) rather than
copying them at import elsewhere — this is what lets tests monkeypatch a
single value and repoint a whole run at test data. Greenhouse board tokens
live in the checked-in `greenhouse_boards.toml`, not `.env`, since they
aren't secret and are easier to grow as a TOML array-of-tables than a
comma-separated env var.

**`models.py`** defines `Job`, the one shape every board client must
produce, and `FIELDS`, the column order written to (and read back from) the
sheet — keep `FIELDS` in sync with `sheets.writer.HEADER`. This is the
seam for adding a board: a new `jobscraper/<board>/client.py` exposing
`fetch_jobs(token) -> list[Job]`, wired into `main.py`'s loop, is the only
change needed elsewhere.

**`greenhouse/client.py`** has one function, `fetch_jobs`, hitting
Greenhouse's public unauthenticated Job Board API. No retry/backoff: any
HTTP error propagates straight up and aborts the whole run (see
`main.py`'s `run()`, which doesn't catch per-board either). This is a
known, deliberate gap at the project's current scale, not an oversight.

**`sheets/writer.py`** is the part most likely to surprise someone reading
only one file at a time — its two functions both key on `(board, job_id)`,
not on age:
- `append_jobs` reads the sheet's existing keys first and skips any job
  already present, so a still-open posting gets exactly one row for its
  lifetime (its `scraped_date` is therefore "first seen," never updated
  again).
- `prune_closed_postings` deletes a row only once its `job_id` no longer
  comes back in that board's fetch — i.e. actually closed on Greenhouse,
  not merely old. It takes `fetched_boards` as an explicit argument rather
  than inferring it from the jobs list, specifically so a board that
  failed to fetch isn't mistaken for "every one of its postings just
  closed." `main.py` currently passes every configured board as
  `fetched_boards`, which is only safe because a single board's fetch
  failure today aborts the run before pruning is ever reached — if
  per-board retry/error-handling is added later, this needs to become the
  set of boards that actually succeeded that run, or a silently-failed
  board would have its still-open postings deleted.

Both `append_jobs` and `prune_closed_postings` do their own independent
`worksheet.get_all_values()` read (two reads per run, not shared) — kept
that way deliberately so each function stays self-contained and testable
in isolation, matching the existing pattern rather than threading sheet
state through `main.py`.

Pruning always rewrites the whole sheet in one `clear()` + `update()` call
rather than deleting rows one at a time, to stay under Sheets API rate
limits — this makes `clear()`+`update()` two non-atomic calls, so a failure
between them would briefly leave just the header row. Acceptable for a
single-writer daily job.
