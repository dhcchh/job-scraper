# latest-job-scraper

Calls job-board APIs daily and writes the results into a Google Sheet.
**Greenhouse** is the first (and currently only) board.

Every fetched posting is classified into a role category (see "How
classification works" below) and a region, PAC or RoW (see "How region
classification works" below), and written to that (category, region)
pair's own tab in the sheet — e.g. an `"SWE/DE"` posting open in Singapore
goes to the `"SWE/DE - PAC"` tab. Postings matching no category (HR, Legal,
Sales, Recruiting, ...) are dropped as noise rather than written anywhere.
Each row also carries a best-effort `seniority` level (Intern, Junior,
Senior, Manager, ...) read off the title — see "How seniority tagging
works" below.

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
way this project's own migration to firm names did (bulk `update()` per
tab, not `append`/`clear`) — `sheets/backfill.py` doesn't do this part for
you, since it only ever recomputes a column from a row's *current* values,
and the old firm name is gone from `greenhouse_boards.toml` by the time you
run it.

Every row also carries `company_type` — also from `greenhouse_boards.toml`,
same "fact about the firm, not the posting" treatment as `firm`. Every board
configured today is `"Prop Trading/Hedge Fund"`; adding a non-quant firm just
means giving its `[[boards]]` entry a different `company_type` string (see
"Adding a board" below) — there's no fixed list to extend anywhere in code.

## How classification works

`jobscraper/classifications/classify_roles.py` holds `CATEGORIES`: an ordered
list of `(category name, keywords)`. `classify_job` matches a job's title
ONLY (not department -- see below) against each category's keywords in order
(case-insensitive, whole-word/whole-phrase — a plain substring check would
let e.g. "data engineer" match inside "Data Engineering Recruiter") and
returns the first category that matches, or `None` if nothing does. `main.py`
groups all fetched jobs by category, drops the `None` bucket, and (crossed
with region — next section) writes each group to its own tab — see the
module docstring for exactly why order matters (a few categories
deliberately share a broad keyword like "developer" or "quant", so which
one is checked first decides the tie-break).

Title only, deliberately: a department field can be a broad org-unit label
rather than a specific function -- on one real board, a "Business
Development Manager" posting and a "Business Manager" posting both sat under
a department literally named "Quant Management". Matching department too
routed both into QT/QR despite neither being a trading/research role, so
department is excluded from role classification entirely (seniority tagging,
below, still uses it -- that false-positive mode doesn't apply there).

Today's categories: `SWE/DE`, `DA/BI`, `DS/ML/Algos`, `QT/QR`, `SRE/DevOps`
— each needs two same-named tabs in the spreadsheet (one per region, see
below), already set up. Deliberately excluded as out of scope, not an
oversight: IT/network/security/datacenter ops roles, and hardware/silicon
design roles — both fall through to `None` today.

To extend:
- **New keyword for an existing category** — add the string to that
  category's tuple in `classify_roles.py`. That's it.
- **New category** — add a `(name, keywords)` tuple to `CATEGORIES`, then
  create its two tabs (`"{name} - PAC"` and `"{name} - RoW"`, and the usual
  header row, see below) in the spreadsheet. `main.py` discovers categories
  from `CATEGORIES` alone, so no other code changes are needed.

## How region classification works

`jobscraper/classifications/classify_region.py` holds `_PAC_COUNTRIES`:
Singapore, Hong Kong, Australia, Japan, South Korea, China, Taiwan, New
Zealand. `classify_region` reads a job's (already-normalized, see "How
location normalization works" below) `location`, and returns `"PAC"` if
*any* one of its offices' countries is in that set, else `"RoW"` — a
posting listing Singapore alongside London still counts as PAC, since the
point is finding a role a Singapore-based candidate could actually get
into, not requiring every office to be in the region.

This was added because the target audience is based in Singapore and wants
local/regional roles easy to find rather than mixed in with postings from
everywhere else. It's a product choice, not a strict geographic one — e.g.
India (Gurgaon, Mumbai, Bangalore) is RoW here despite being
"Asia-Pacific" in plenty of other contexts.

Combined with role classification above, every posting lands in one of 10
tabs: 5 categories × `("PAC", "RoW")`, named `"{category} - {region}"` (see
`classify_region.tab_name`) — e.g. `"QT/QR - RoW"`. Like every other tab,
nothing auto-creates these; they must already exist with that exact name.

To extend: add a country to `_PAC_COUNTRIES` — but a country alone isn't
enough if none of its cities are in `normalize_location.py`'s table yet
(`classify_region` only ever reads the country a normalized location string
already ends in), so a genuinely new PAC city needs an entry in both
places.

## How seniority tagging works

`jobscraper/classifications/classify_seniority.py` holds `LEVELS`: the same
ordered `(name, keywords)` shape as `CATEGORIES`, but for seniority rather
than role, and matched against `{title} {department}` (department is fine to
include here -- a department like "Internship Program" is a much stronger,
narrower signal than the broad org-unit labels that make it unsafe for role
classification above). It runs once, at fetch time, in
`job_boards/greenhouse/client.py`, and the result is stored on `Job.seniority`
-- so by the time `main.py` runs, every job already has its level, and it
just rides along as one more column on the row (see "How the full-refresh
backfill works" below for what to do if you change `LEVELS` and want that
applied to postings already in the sheet, not just new ones).

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

## How location normalization works

A Greenhouse job's `location.name` is one free-text string a recruiter
typed, and different boards format it inconsistently — e.g. Squarepoint
writes multi-location postings as `"Jersey, Montreal, Warsaw"` (comma
between cities), while HRT writes `"London, United Kingdom; New York, NY,
United States"` (semicolon between locations, comma *inside* one). No
regex can reliably tell those two comma uses apart, so `location.name`
isn't parsed at all. Instead, `job_boards/greenhouse/client.py` reads
`offices` — a second, *pre-split* field Greenhouse's API also returns
(available because `fetch_jobs` always requests `content=true`) where each
office is already its own entry, e.g. `[{"name": "London"}, {"name": "New
York City"}]`. Each office's name is run through
`classifications/normalize_location.py`'s lookup table (fixing spelling
drift like `"New York City"`/`"NYC"`/`"New York, NY"` all meaning the same
city), de-duplicated, and joined with `" | "` — chosen over `","` since a
normalized name could in principle contain a comma itself, which would
reintroduce the exact ambiguity this whole thing exists to avoid. A
posting with no `offices` data falls back to the raw `location.name`,
still normalized the same way.

`normalize_location.py`'s table was seeded the same way
`classify_roles.py`'s keyword lists were — by dry-running against every
board in `greenhouse_boards.toml` today — so it'll need the same kind of
tuning pass as new firms (and their offices) are added. An office name not
in the table is left as-is (cleaned, not guessed at) rather than
fabricating a city/country for a label that doesn't clearly name one (e.g.
a bare `"Australia"`, or an internal Greenhouse label that isn't really a
location at all).

## How dedup and pruning work

This is the core loop in `main.py`, per run:

1. Fetch every configured board → `all_jobs`.
2. Classify each job into a category (above) and, if it has one, a region
   (above too); jobs matching no category are dropped.
3. Per (category, region) tab: `append_jobs` reads that tab's existing
   `(board, job_id)` pairs, and appends only the jobs not already there. A
   posting that's still open on day 30 is simply left alone — its original
   row, and original `scraped_date`, stand untouched.
4. Per (category, region) tab: `prune_closed_postings` -- for every board
   that was just fetched, any *existing* row in that tab whose `job_id`
   didn't come back in today's fetch (for that category+region) gets
   deleted — Greenhouse no longer lists it, so it's closed. A board isn't
   considered for pruning at all unless it's in `fetched_boards` — see the
   two edge cases below.

Row order is left entirely to you: this script only appends new rows and
deletes closed ones, it never reorders what's already there, so any sort or
filter you set up in Sheets survives every run.

Why age-based pruning (delete anything older than N days) was replaced:
"still open" is the only real "still hiring" signal the Greenhouse API
gives you, and it's a poor proxy for "worth applying to" — some firms run
rolling admissions and quietly auto-reject well before ever taking a
listing down, which no amount of pruning logic can detect either way.
Rather than guess at that, the sheet just tracks ground truth (open vs.
closed) and leaves `scraped_date` (first-seen date) as the input for you
to apply your own judgment on staleness.

Two edge cases worth knowing if you're reading `sheets/writer.py`:
- A board that fails to fetch after retries (see below), or gets removed
  from `greenhouse_boards.toml`, must **not** be passed in `fetched_boards`,
  or every one of its existing rows would look closed and get deleted.
  `main.py` builds `fetched_boards` from only the firms whose fetch actually
  succeeded that run — and for a firm with more than one board token
  (Optiver has two), only if *all* of its tokens succeeded, since they share
  one dedup key and a partial success would otherwise make the failed
  token's postings look closed.
- A board that's fetched successfully but currently has **zero** open
  postings is still fully pruned — its rows correctly disappear, since an
  empty result is a legitimate "this board's listings are all closed" state,
  not a sign the fetch didn't happen.

## How the full-refresh backfill works

```bash
uv run python -m jobscraper.sheets.backfill
```

`main.py`'s daily run never rewrites a row that's already in the sheet
except to delete it (`append_jobs` only adds new rows; `prune_closed_postings`
only rewrites a tab when it actually has something to delete from it, and
even then preserves whatever header/column order that tab already had). So
a change to classification logic, or to `models.FIELDS`' column order,
never reaches an already-written row on its own — only new ones. Run this
script by hand after such a change to apply it retroactively, tab by tab:

- Recomputes `seniority` from each row's own `title`/`department`, via
  whatever `classify_seniority.py` currently says — e.g. after fixing its
  "misses numeric/leveling titles" gap.
- Recomputes `company_type` by looking each row's `board` up in
  `greenhouse_boards.toml` — e.g. after re-categorizing a firm there.
- Re-normalizes `location` through `normalize_location.py`'s current
  table, best-effort — see "How location normalization works" above.
- Re-lays-out every row into the *current* `models.FIELDS` order and
  overwrites the header to match — this is what makes a `FIELDS` reorder
  (like the one this project ships with today) safe to deploy: run this
  once right after, before the next scheduled `main.py` run, so newly
  appended rows line up with the header instead of landing under the wrong
  column.
- Re-submits every value with `USER_ENTERED`, same as `writer.py`'s own two
  functions, so an older row's plain-text date/url also becomes a real
  Sheets date / an auto-linked url.

Not handled — see `sheets/backfill.py`'s module docstring for why: moving a
row to a different category or region tab if `classify_roles.py`'s
keywords or `classify_region.py`'s PAC countries change, and rewriting the
`board` column after a firm rename (see "The `board` column..." above).

## What's built vs. what's left for you

Built (working end-to-end):
- Greenhouse Job Board API client
  (`src/jobscraper/job_boards/greenhouse/client.py`)
- Keyword-based role classification into per-category sheet tabs, dropping
  everything else as noise
  (`src/jobscraper/classifications/classify_roles.py`)
- Keyword-based seniority tagging (Intern/Junior/Senior/Manager/...) on every
  row (`src/jobscraper/classifications/classify_seniority.py`)
- Location normalization — per-office (not naive comma-split) extraction and
  a curated city-name lookup table
  (`src/jobscraper/classifications/normalize_location.py`)
- Region classification (PAC/RoW) crossed with role category, into 10
  sheet tabs (`src/jobscraper/classifications/classify_region.py`)
- Google Sheets append-if-new + prune-closed-postings, per tab
  (`src/jobscraper/sheets/writer.py`)
- Full-refresh backfill — recompute derived columns and re-lay-out column
  order across every already-written row (`src/jobscraper/sheets/backfill.py`)
- Config/env var loading, orchestration (`config.py`, `main.py`)
- Daily GitHub Actions cron (`.github/workflows/daily-scrape.yml`)

Also built: retries with backoff on transient Greenhouse errors, and
per-board error handling — a board that still fails after retries is
skipped (logged, not raised), so it doesn't abort every other board's
update for the day.

Deliberately **not** built — the natural next things to add yourself:
- More job boards (Lever, Workday, ...) — add a sibling package under
  `src/jobscraper/job_boards/`, expose `fetch_jobs(token) -> list[Job]`,
  wire it into `main.py`'s loop.
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
4. **Worksheet/tabs** — one tab per (category, region) pair: each category
   in `jobscraper/classifications/classify_roles.py`'s `CATEGORIES`
   (currently `SWE/DE`, `DA/BI`, `DS/ML/Algos`, `QT/QR`, `SRE/DevOps`)
   crossed with each region in `classify_region.py`'s `REGIONS` (`PAC`,
   `RoW`), named exactly `"{category} - {region}"` (e.g. `"SWE/DE - PAC"`,
   `"SWE/DE - RoW"`) — 10 tabs today. Each needs a header row exactly
   matching `jobscraper.models.FIELDS`:
   `board, title, location, updated_at, company_type, url, seniority, department, job_id, scraped_date`
   This project does not create tabs (or missing header columns) for you.
   Order is meant to put what you'd actually filter/sort by first (company,
   title, location, updated_at, company_type); `job_id`/`scraped_date` are
   pure bookkeeping and sit last.

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
company_type = "Big Tech"
```

`token` is the slug from the company's Greenhouse careers URL
(`boards.greenhouse.io/<token>`) — that's what's used to fetch the board.
`firm` and `company_type` both end up as columns on every job from this
board (see "The `board` column..." above) — `firm` is the readable name
shown instead of `token`, and `company_type` is a free-form label (every
board today is `"Prop Trading/Hedge Fund"`; a non-quant firm might be
`"Big Tech"`, `"Startup"`, or anything else you find useful to sort/filter
by later — there's no fixed list to update anywhere in code). Commit and
push — no other config changes needed, since this file (unlike `.env`) is
checked into the repo and read directly by both local runs and CI.

## Cron / CI

`.github/workflows/daily-scrape.yml` runs at `0 4 * * *` UTC (12:00
Singapore time, UTC+8, no DST), plus a manual `workflow_dispatch` trigger for
testing. It needs two repository secrets:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

GitHub Actions only picks up workflows from a repository's own root
`.github/workflows/` — this project needs to live in its own git repo (not
nested inside a parent monorepo) for the schedule to fire.
