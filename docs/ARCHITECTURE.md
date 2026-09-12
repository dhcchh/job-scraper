# Architecture

A component-by-component reference for `latest-job-scraper`.

## Source layout

```
src/jobscraper/
├── __init__.py
├── config.py                          # Env vars + greenhouse_boards.toml loader
├── main.py                            # Entrypoint: fetch → classify → append/prune
├── models.py                          # Job dataclass, FIELDS column order, helpers
├── classifications/
│   ├── classify_region.py             # PAC / RoW region routing
│   ├── classify_roles.py              # Role-category keyword classifier
│   ├── classify_seniority.py          # Seniority-level keyword classifier
│   └── normalize_location.py          # Office name → canonical "City, Country"
├── job_boards/
│   └── greenhouse/
│       └── client.py                  # Greenhouse Job Board API fetcher
└── sheets/
    ├── writer.py                      # Append new rows, prune closed postings
    └── backfill.py                    # Re-apply current classifiers to existing rows
```

## Data flow

```
greenhouse_boards.toml        Greenhouse Job Board API
        │                              │
        ▼                              ▼
    config.py ──────────────► greenhouse/client.py
                                       │
                              fetch_jobs() → list[Job]
                                       │
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                  classify_roles  classify_region  (seniority already
                    → category     → PAC/RoW       set at fetch time)
                          │            │
                          └─────┬──────┘
                                ▼
                     tab = "{category} - {region}"
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              append_jobs()        prune_closed_postings()
             (sheets/writer.py)     (sheets/writer.py)
                    │                       │
                    └───────────┬───────────┘
                                ▼
                         Google Sheets
```

## Components

### `config.py`

The single source of truth for all external configuration. Reads environment
variables (`GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`) and parses
`greenhouse_boards.toml` at import time. Every other module imports `config`
rather than calling `os.environ` directly, so tests can monkeypatch one
central place.

**Key exports:** `GREENHOUSE_BOARDS`, `GOOGLE_SHEET_ID`,
`GOOGLE_SERVICE_ACCOUNT_JSON`.

### `models.py`

Defines the `Job` dataclass — the shared data shape every board client must
produce. Also owns:

- **`FIELDS`** — the canonical column order for the Google Sheet. Adding,
  removing, or reordering fields here is a live-sheet migration (run
  `backfill.py` afterward).
- **`hyperlink_formula(url, label)`** — wraps a URL in a Sheets
  `=HYPERLINK(url, "Apply")` formula so the url column shows a short
  clickable label instead of the raw URL.
- **`normalize_date(value)`** — coerces any date/datetime/string into
  `YYYY-MM-DD` for uniform Sheets date formatting.
- **`Job.to_row()`** — renders a Job into a flat list in `FIELDS` order,
  ready to be written as a sheet row.

**Identity:** A posting is uniquely identified by `(board, job_id)`, not
`job_id` alone — Greenhouse IDs are tenant-scoped.

### `main.py`

The CLI entrypoint (`python -m jobscraper.main`). Orchestrates one full
scrape cycle:

1. Opens the Google Sheet and logs its title/URL (drift detection).
2. Fetches every configured Greenhouse board, collecting `Job` objects.
   Failed boards are skipped for pruning but tracked for the summary.
3. Classifies each job by role category and region → picks a tab.
4. Appends new jobs and prunes closed postings per tab.
5. Writes a GitHub Actions job summary (when `$GITHUB_STEP_SUMMARY` is set).
6. Raises if any board failed, so CI doesn't report a partial run as healthy.

Multi-token firms (e.g. Optiver) are only pruned if *all* their tokens
succeeded, preventing a single failed token from looking like mass closures.

### `classifications/classify_roles.py`

Keyword-matches a job's **title** into one of 5 role categories:

| Category | Examples |
|----------|----------|
| DS/ML/Algos | data scientist, machine learning, algo, research engineer |
| DA/BI | data analyst, business intelligence, bi developer |
| SWE/DE | software engineer, backend, data engineer, developer |
| QT/QR | quant, quantitative, execution trader |
| SRE/DevOps | site reliability, devops, platform engineer |

Jobs matching none of these (HR, Legal, Sales, etc.) are dropped as noise.
Recruiter titles are vetoed before category matching so "Campus Recruiter,
Machine Learning" doesn't leak into DS/ML/Algos.

**Matching:** case-insensitive whole-word regex (`\b...\b`) against title
only. Category order matters — DS/ML/Algos and DA/BI are checked before
SWE/DE so "Algo Developer" and "BI Developer" resolve correctly.

### `classifications/classify_region.py`

Routes a job's normalized location into **PAC** (Singapore, Hong Kong,
Australia, Japan, South Korea, China, Taiwan, New Zealand) or **RoW**
(everywhere else). "Any office counts" — a posting listing Singapore
alongside London is PAC.

Combined with the role category, this picks one of the 10 spreadsheet tabs
(5 categories × 2 regions), named `"{category} - {region}"`.

### `classifications/classify_seniority.py`

Tags each job with a seniority level from the title (+ department):

`Director/VP/Head > Manager > Principal > Staff > Lead > Senior > Mid > Junior > Intern > Graduate > Unspecified`

~72% of real postings carry no seniority wording ("Unspecified") — this is
genuine ambiguity, not a bug. First match wins, so "Senior Manager" resolves
to Manager (the more specific/senior tier).

### `classifications/normalize_location.py`

Canonicalizes one Greenhouse office name into a consistent `"City, Country"`
string via a hand-curated lookup table. Handles noise like `" Office"`
suffixes, `"Remote - "` prefixes, `"(Remote)"` suffixes, and Stripe-style
`"Location(s)"` wrappers. Unmapped names pass through cleaned but unchanged
— the module never guesses.

This module normalizes **one** name at a time. Splitting a posting's
multiple offices is the Greenhouse client's job (using `offices[]`), not
this module's.

### `job_boards/greenhouse/client.py`

Fetches every open posting from one Greenhouse board via the public Job
Board API (`boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`).

- **Retries:** 3 attempts total with exponential backoff (2s, 4s) for
  connection errors, timeouts, 5xx, and 429. Non-retryable errors (other
  4xx) raise immediately.
- **Location:** Built from `offices[]` (pre-split by Greenhouse), not the
  ambiguous `location.name` free-text field.
- **Seniority:** Computed at fetch time via `classify_seniority()` so it's
  already set on the `Job` before `main.py` sees it.

### `sheets/writer.py`

Reads/writes the Google Sheet via `gspread`:

- **`append_jobs(worksheet, jobs)`** — deduplicates by `(board, job_id)`
  against existing rows, then appends only genuinely new postings. Uses
  `USER_ENTERED` value input so dates and `=HYPERLINK()` formulas are
  parsed.
- **`prune_closed_postings(worksheet, current_jobs, fetched_boards)`** —
  deletes rows for postings no longer in the current fetch. Only prunes
  boards that were successfully fetched. Uses an atomic `batchUpdate` of
  `deleteDimension` requests (bottom-up) so a failure leaves the sheet
  untouched.
- **`format_date_columns(worksheet)`** — explicitly formats date columns
  as `yyyy-mm-dd`.

Row order is never touched — user sort/filter setups survive every run.

### `sheets/backfill.py`

Manual-only script (`python -m jobscraper.sheets.backfill`). Re-applies
current classifiers, location normalization, and column ordering to every
existing row in the sheet. Run it after changing:

- `FIELDS` order
- Classification keywords or seniority levels
- Location lookup table entries
- The `hyperlink_formula` wrapping (migrates legacy plain URLs)

It does **not** move rows between tabs or rename board values — operations
that could lose data if interrupted.

For the url column specifically, it reads with `value_render_option="FORMULA"`
to recover the real URL behind an already-migrated `=HYPERLINK()` cell,
since a normal `FORMATTED` read would return only the display label.

## Configuration files

### `greenhouse_boards.toml`

Checked-in list of Greenhouse boards to scrape. Each entry has:

```toml
[[boards]]
token = "stripe"          # Greenhouse career-page slug
firm = "Stripe"           # Readable name → board column
company_type = "Big Tech" # Free-form label
```

Board tokens are **not secrets** — they're public career-page slugs.

### `.github/workflows/daily-scrape.yml`

Runs at `0 4 * * *` UTC (noon Singapore time) plus manual dispatch.
Uses `concurrency: { group: daily-scrape, cancel-in-progress: false }` to
queue rather than cancel overlapping runs. Requires two repo secrets:
`GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`.

## Test structure

```
tests/
├── conftest.py                        # Dummy config + fixtures (no network/creds)
├── classifications/
│   ├── test_classify_region.py        # PAC/RoW routing
│   ├── test_classify_roles.py         # Category keyword matching
│   ├── test_classify_seniority.py     # Seniority level matching
│   └── test_normalize_location.py     # Office name canonicalization
├── greenhouse/
│   └── test_client.py                 # API fetch + retry logic
├── sheets/
│   ├── test_writer.py                 # Append/prune/dedup
│   └── test_backfill.py               # Row rewriting + url migration
├── test_config.py                     # TOML parsing
├── test_main.py                       # End-to-end orchestration
└── test_models.py                     # Job.to_row(), hyperlink_formula, normalize_date
```

All tests run offline — no network calls or live Google credentials. Sheet
tests use a lightweight `_FakeWorksheet` stub.
