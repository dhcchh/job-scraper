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
Greenhouse's public unauthenticated Job Board API. Transient failures
(connection errors, timeouts, 5xx, 429) are retried up to 3 attempts total
with exponential backoff; anything else (a 4xx like a bad/renamed token)
raises immediately since retrying can't fix it. `main.py`'s `run()` catches
per-board on top of that — a board that still fails after retries is
logged and skipped rather than aborting the whole run, so one bad board or
a sustained Greenhouse outage no longer takes down every other board's
update.

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
  closed." `main.py` passes only the firms that actually succeeded this
  run — and for a firm with more than one board token (Optiver has two),
  only if *all* of its tokens succeeded, since the two share one dedup key
  and a partial success would otherwise make the failed token's postings
  look closed.

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

## Known gaps / next improvements

Not urgent, but worth doing roughly in this order as the project grows —
especially once non-quant firms are added to `greenhouse_boards.toml`:

- **Seniority levels miss numeric/leveling titles** — `classify_seniority.py`
  only matches English seniority words (Senior, Manager, ...); big-tech-style
  titles ("Software Engineer II", "SDE II", "L4/L5") fall through to
  `Unspecified` today. Likely to matter as soon as non-quant firms are added.
- **Role classifier needs a validation pass per new firm type** —
  `classify_roles.py`'s keyword lists were tuned by dry-running against the
  current 15 quant-firm boards' real postings. A firm with different title
  conventions for the same 5 categories will likely need the same kind of
  tuning pass; don't assume it Just Works untested.
- **No CI test run on push** — tests only run when someone runs
  `uv run pytest` manually; nothing blocks a broken commit from reaching
  `master` before the next scheduled scrape. Worth a workflow that runs
  `uv run pytest` on push/PR.
- **No run notifications** — success/failure/job-counts aren't reported
  anywhere but the run log; a silent failure is only caught by manually
  checking the sheet or GitHub Actions.
- **No worksheet auto-creation** — `sheets/writer.get_worksheet()` raises if
  a tab doesn't already exist; fine while tabs are managed by hand.
- **No fail-fast config validation** — a `[[boards]]` entry missing
  `company_type`/`firm`/`token` surfaces as a bare `KeyError` in `main.py`'s
  loop, not a message naming the bad entry.
- **Location isn't normalized** — multi-location postings are one combined
  string (e.g. "London, Montreal, Singapore"), sometimes with stray leading
  whitespace; fine today, would matter for per-city filtering later.
