# latest-job-scraper

Fetches job postings from Greenhouse boards daily, classifies them, and writes them into a Google Sheet.

## Run it

```bash
uv sync
cp .env.example .env                       # fill in, see Setup below

uv run python -m jobscraper.main           # run the scraper once
uv run python -m jobscraper.sheets.backfill  # re-apply current classification/formatting to existing rows
uv run pytest                              # tests -- no network or credentials needed
```

## Setup

1. **Greenhouse board tokens** — add each firm as a `[[boards]]` entry in
   `greenhouse_boards.toml` (repo root, checked in — see "Adding a board").
2. **Google Sheets access** — in Google Cloud Console: enable the Sheets
   API, create a service account + JSON key, then **Share** your target
   sheet with that key's `client_email` as **Editor**. Collapse the key to
   one line and paste it as `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env`:
   `python -c "import json;print(json.dumps(json.load(open('key.json'))))"`
3. **Sheet ID** — the id segment of the sheet's URL, as `GOOGLE_SHEET_ID`
   in `.env`.
4. **Tabs** — one per (role category × region) pair, named exactly
   `"{category} - {region}"` (e.g. `"SWE/DE - PAC"`) — see `CATEGORIES` in
   `classify_roles.py` and `REGIONS` in `classify_region.py` for the
   current full list (10 tabs today). Each needs a header row matching
   `jobscraper.models.FIELDS` exactly:
   `board, title, location, updated_at, company_type, url, seniority, department, job_id, scraped_date`.
   Tabs and header rows aren't auto-created.

## How it works

- **Role category**: `classify_roles.py` keyword-matches each job's title
  into one of `CATEGORIES` (or drops it as noise if nothing matches).
- **Region (PAC/RoW)**: `classify_region.py` buckets a job's location into
  `"PAC"` (Singapore/Hong Kong/Australia/Japan/South Korea/China/Taiwan/New
  Zealand) or `"RoW"`, so the target Singapore-based audience can find
  local roles without wading through everything else. Category × region
  picks the tab.
- **Location normalization**: `normalize_location.py` cleans up each
  office name via a curated lookup table, reading Greenhouse's pre-split
  `offices` field rather than parsing the ambiguous free-text
  `location.name` string — see the module docstrings for why that field
  can't be safely comma-split.
- **Seniority**: `classify_seniority.py` tags a best-effort level
  (Intern/Junior/.../Unspecified) off the title, computed once at fetch time.
- **Identity**: a posting is uniquely identified by `(board, job_id)`, not
  `job_id` alone — Greenhouse assigns ids per tenant, so two different
  firms' boards can (and do) reuse the same numeric id. `board` is the
  firm's readable name from `greenhouse_boards.toml` (not the Greenhouse
  token), and `job_id` is that board's own id for the posting, kept as a
  string.
- **Dedup/pruning**: keyed on that same `(board, job_id)` pair — a run
  appends a row the first time it sees one, never re-appends it while
  still open, and deletes it once it's gone from that board's feed. Row
  order is never touched, so your own sort/filter setup survives every run.

See `CLAUDE.md`'s Architecture section for the full design rationale
(edge cases, why each choice was made) — this file only covers usage.

## Scale limits

This design — one GitHub Actions run, boards fetched one at a time, a
Google Sheet as the datastore — works fine at today's ~15 boards. Realistic
ceiling: **somewhere around 2,000-5,000 companies**, not the 10,000+ the
raw minutes math alone would suggest:

- **GitHub Actions minutes, idealized**: fetching is sequential (see
  `job_boards/greenhouse/client.py`), ~0.25s/board observed (15 boards ≈
  4s total). This repo is a private GitHub repo, so its free tier is 2,000
  Actions minutes/month, shared across every private repo on the account.
  At 0.25s/board with negligible fixed overhead, that budget alone isn't
  exhausted until roughly **~15,000 companies** — higher than you'd guess.
- **...but that's optimistic.** Retries/backoff on transient failures
  (2s/4s each) aren't in that math, and at real scale some % of boards
  fail transiently every run. `get_all_values()` also re-reads each tab's
  *entire* accumulated history every run, so runtime grows with total
  tracked postings over time, not just company count that day.
- **The real unknown: Greenhouse itself.** This design hits thousands of
  different Greenhouse-hosted board subdomains sequentially from one CI
  runner IP. Each board is a different tenant, but Greenhouse's own
  infrastructure is likely shared at the network/WAF layer — untested
  whether/when that starts looking like abusive traffic and gets
  throttled, independent of anything on this side.
- **Sheets' own hard cap**: a spreadsheet holds at most 10,000,000 cells
  (~1,000,000 rows at `FIELDS`' 10 columns). Where this binds depends on
  postings-per-company; today's sample (large quant/trading firms, ~45
  classified postings/board average) would hit it well under 10,000
  companies, but a broader, smaller-company mix would likely push this
  ceiling out past 10,000.

Past a few hundred boards, this needs concurrent/batched fetching, a real
database instead of a spreadsheet, and a dedicated scheduler/worker instead
of a GitHub Actions cron.

## Adding a board

```toml
[[boards]]
token = "stripe"          # slug from boards.greenhouse.io/<token>
firm = "Stripe"           # readable name, shown in the board column
company_type = "Big Tech" # free-form label, no fixed list
```

Commit and push — `greenhouse_boards.toml` is checked in and read directly
by both local runs and CI, no other config changes needed.

## Cron / CI

`.github/workflows/daily-scrape.yml` runs at `0 4 * * *` UTC (noon
Singapore time), plus manual `workflow_dispatch`. Needs two repo secrets:
`GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`.
