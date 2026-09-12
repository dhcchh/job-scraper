# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                        # install dependencies
cp .env.example .env           # then fill in (see README's Setup section)

uv run python -m jobscraper.main       # run the scraper once, locally
uv run python -m jobscraper.sheets.backfill  # full-refresh every existing row/tab (see Architecture)

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
sheet — keep `FIELDS` in sync with `sheets.writer.HEADER`. Order is chosen
for a human scanning the sheet, not for `Job`'s constructor: the columns
someone actually filters/sorts by (`board`, `title`, `location`,
`updated_at`, `company_type`) come first, `url` keeps a near-front spot
since it's the one column meant to be clicked, and `job_id`/`scraped_date`
(pure bookkeeping) sit last. `Job`'s own dataclass field order is untouched
by any of this — only `FIELDS` and `to_row()` encode sheet column order, so
reordering columns never means touching every `Job(...)` call site. This is
also the seam for adding a board: a new `jobscraper/<board>/client.py`
exposing `fetch_jobs(token) -> list[Job]`, wired into `main.py`'s loop, is
the only change needed elsewhere.

**`greenhouse/client.py`** has one function, `fetch_jobs`, hitting
Greenhouse's public unauthenticated Job Board API. Transient failures
(connection errors, timeouts, 5xx, 429) are retried up to 3 attempts total
with exponential backoff; anything else (a 4xx like a bad/renamed token)
raises immediately since retrying can't fix it. `main.py`'s `run()` catches
per-board on top of that — a board that still fails after retries is
logged and skipped rather than aborting the whole run, so one bad board or
a sustained Greenhouse outage no longer takes down every other board's
update.

**`classifications/classify_region.py`** is the other half of what picks a
job's spreadsheet tab, alongside `classify_roles.py`'s category: `main.py`
now writes to 10 tabs (5 categories × `REGIONS`, i.e. `("PAC", "RoW")`),
named via `tab_name(category, region)` → `"{category} - {region}"` — this
must match the tabs already in the spreadsheet exactly, since (like every
other tab) nothing here auto-creates one. `classify_region` reads
`Job.location` (already normalized/`" | "`-joined by `greenhouse/client.py`
— see below) and returns `"PAC"` if *any* one of the job's offices is in
`_PAC_COUNTRIES` (Singapore, Hong Kong, Australia, Japan, South Korea,
China, Taiwan, New Zealand), else `"RoW"` — "any, not every" is deliberate:
a posting listing Singapore alongside London still counts as one a
Singapore-based candidate could get into.

**`classifications/normalize_location.py`** canonicalizes one office name
via a hand-curated lookup table (same shape/philosophy as
`classify_seniority.py`'s `LEVELS` — unmapped falls through cleaned but
unchanged, deliberately, rather than guessing). The non-obvious part lives
in `greenhouse/client.py`'s `_location`, which is what calls it: a
Greenhouse job's `location.name` is one free-text string a recruiter typed
(e.g. `"London, United Kingdom; New York, NY, United States"`) and can't be
safely split on a delimiter — different boards mix commas *inside* one
location (city, state, country) with commas *and* semicolons *between*
locations, inconsistently. `raw["offices"]` (present because `fetch_jobs`
always requests `content=true`) is Greenhouse's own pre-split per-office
list instead, so `_location` builds `Job.location` from that — normalizing
and de-duplicating each office name, then joining with `" | "` — and only
falls back to the raw `location.name` string if `offices` is ever
missing/empty. `classify_region` above depends on this: it only ever reads
the country `normalize_location` already put after the last comma, so a
new PAC city needs an entry here before it's recognized as PAC there too.

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

Both `append_jobs` and `prune_closed_postings` write with
`value_input_option="USER_ENTERED"`, not `"RAW"`, so Sheets parses each
value the way a human typing it would — `scraped_date` becomes a real date
(sortable/filterable/formattable as one, not plain text) and `url` gets
auto-linkified. This is the one piece of "human-readable output" that has
to live in code rather than be set up by hand in Sheets, since it's about
how a value is interpreted at write time, not how the sheet looks — see
"Known gaps" below for the presentation half of that split.

**`sheets/backfill.py`** is the retroactive counterpart to `writer.py`:
`append_jobs` only ever touches brand-new rows, and `prune_closed_postings`
rewrites a tab (preserving its existing header/column order as-is) only
when it actually has a row to delete — so a classification fix, a
`company_type` re-tag in `greenhouse_boards.toml`, or a `FIELDS` reorder
never reaches an already-written row on its own, and a quiet tab (nothing
ever closes) could sit on stale data indefinitely. `refresh_worksheet` reads
each tab's *current* header (whatever order it happens to be in), recomputes
`seniority`/`company_type` from each row's own columns, then re-lays every
row out into the *current* `models.FIELDS` order and overwrites the header
to match — run by hand (`uv run python -m jobscraper.sheets.backfill`),
never from `main.py`'s daily loop. Run it once right after any `FIELDS`
reorder ships, before the next scheduled run, or `append_jobs`'s
`Job.to_row()` output (always in `FIELDS` order) will land under whatever
stale header the tab still has. It also re-normalizes `location` through
`normalize_location.py`'s current table, best-effort: a cell already
`" | "`-joined by a previous backfill run gets fully re-cleaned per piece,
but a cell written before location normalization existed at all is one
un-split legacy string with no `offices` data left to recover — it's
normalized as one whole string (fixes it if it was a single plain city
name) and otherwise left alone rather than guessed at, self-healing only
once that posting naturally closes and reopens through `fetch_jobs`. It
deliberately does not re-route a job to a different category *or region*
tab (moving rows across tabs risks duplicating/losing data if interrupted)
or rewrite
`board` after a firm rename (the old firm name is already gone from
`greenhouse_boards.toml` by the time this runs, so there's nothing to look
it up against) — see its module docstring.

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
- ~~Location isn't normalized~~ — done, see `classifications/normalize_location.py`
  in Architecture above.
- **No visual highlight for a run's newly-scraped rows** — nothing marks
  which rows came from today's run vs. an older one. This is presentation,
  not data, so it belongs in the Sheets UI as a conditional formatting rule
  keyed on `scraped_date` (e.g. highlight rows where `scraped_date =
  TODAY()`), set up once by hand rather than in the script. Like all sheet
  formatting, it survives every run for free: `prune_closed_postings`'s
  `clear()`+`update()` only touches cell values via the Sheets API, never
  formatting.
- **No default sort/visual ordering** — row order is deliberately left to
  the end user (see commit 99a8568 and `main.py`'s docstring: the script
  never reorders existing rows, so a user's own sort/filter setup survives
  every append/prune). A default "most recent first" view should likewise
  be set up once in the sheet itself — a Sheets sort (Data > Sort range) or
  a Filter view keyed on `scraped_date` — not code that re-sorts rows on
  every run, which would undo it.
- ~~Region refactor~~ — done, see `classifications/classify_region.py` in
  Architecture above. Turned out to be 10 tabs (5 categories × PAC/RoW), not
  8 — confirmed against the actual spreadsheet tab names.