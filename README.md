# latest-job-scraper

Fetches job postings from Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, and Workday boards daily, classifies them, and writes them into a Google Sheet.

## Run it

```bash
uv sync
cp .env.example .env                       # fill in, see Setup below

uv run python -m jobscraper.main           # run the scraper once
uv run python -m jobscraper.sheets.backfill  # re-apply current classification/formatting to existing rows
uv run python -m jobscraper.sheets.full_refresh  # rebuild and re-route every sheet row
uv run python -m jobscraper.telegram --check # send a one-off test message, to verify Telegram setup
uv run pytest                              # tests -- safe to run even with a real .env, see below
```

Tests never hit the network or a real Google/Telegram account, even when
`.env` has real credentials in it: `tests/conftest.py` forces
`GOOGLE_SHEET_ID`/`GOOGLE_SERVICE_ACCOUNT_JSON`/`TELEGRAM_BOT_TOKEN`/
`TELEGRAM_CHAT_ID`/`TELEGRAM_THREAD_ID` to dummy/empty values before
`jobscraper.config` ever loads `.env` (its `load_dotenv()` never overrides
an already-set variable), and sheet/Telegram-sending code itself is mocked
at the call site too. Skipping the Telegram half of this once let a real
`.env`'s real bot token/chat id flow through into `uv run pytest`, which
posted every test run's fake job data straight to a real Telegram chat --
fixed, but worth knowing if you're adding another `.env`-configured
integration later: add its vars here too.

## Setup

1. **Board tokens** — add each firm as a `[[boards]]` entry in
   `company_toml/greenhouse_boards.toml`, `company_toml/lever_boards.toml`,
   `company_toml/ashby_boards.toml`,
   `company_toml/smartrecruiters_boards.toml`,
   `company_toml/eightfold_boards.toml`, or
   `company_toml/workday_boards.toml` (checked in — see "Adding a board").
2. **Google Sheets access** — in Google Cloud Console: enable the Sheets
   API, create a service account + JSON key, then **Share** your target
   sheet with that key's `client_email` as **Editor**. Collapse the key to
   one line and paste it as `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env`:
   `python -c "import json;print(json.dumps(json.load(open('key.json'))))"`
3. **Sheet ID** — the id segment of the sheet's URL, as `GOOGLE_SHEET_ID`
   in `.env`. Optionally also set `GOOGLE_SHEET_TITLE` to the sheet's exact
   display title — since this id is set independently in `.env` and in the
   GitHub Actions secret, this catches the two drifting apart (a typo'd or
   stale id pointing at the wrong file) as a startup failure instead of a
   silent write to the wrong spreadsheet.
4. **Tabs** — one per (role category × region) pair, named exactly
   `"{category} - {region}"` (e.g. `"SWE/DE - PAC"`) — see `CATEGORIES` in
   `classify_roles.py` and `REGIONS` in `classify_region.py` for the
   current full list (10 tabs today). Each needs a header row matching
   `jobscraper.models.FIELDS` exactly:
   `first_published, updated_at, board, title, location, seniority, company_type, url, salary, department, job_id, scraped_date, source, description`.
   Tabs and header rows aren't auto-created.
5. **Telegram digest (optional)** — set `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, and (if the chat has topics) `TELEGRAM_THREAD_ID` in
   `.env` to get one batched Telegram message per run summarizing that run's
   newly appended DA/BI, DS/ML/Algos, and SWE/DE postings (PAC only) as
   grouped counts (e.g. "3 new SWE/DE roles from Grab available in
   Singapore") with a link back to the sheet, not a per-job listing. Leave
   any of the three unset to skip this entirely — see `telegram.py`. Run
   `uv run python -m jobscraper.telegram --check` after setting these to
   confirm the bot can actually reach the chat, without waiting for a real
   run to have new PAC postings to report.

## Architecture

```
company_toml/                          # *_boards.toml -- checked-in board configs, not secrets
src/jobscraper/
├── config.py                          # only module reading env vars / company_toml/*_boards.toml
├── main.py                            # entrypoint: fetch → classify → append/prune, per board
├── models.py                          # Job dataclass, FIELDS column order, to_row/date helpers
├── telegram.py                        # optional per-run Telegram digest (DA/BI, DS/ML/Algos, SWE/DE, PAC only)
├── enrichment/
│   ├── classify_roles.py              # title → role category (or dropped as noise)
│   ├── classify_region.py             # location → PAC/RoW
│   ├── classify_seniority.py          # title/department → seniority level
│   └── normalize_location.py          # one office name → canonical "City, Country"
├── job_boards/
│   ├── greenhouse/client.py           # Greenhouse Job Board API fetch + retry
│   ├── lever/client.py                # Lever Postings API fetch + retry
│   ├── ashby/client.py                # Ashby Posting API fetch + retry
│   ├── smartrecruiters/client.py      # SmartRecruiters Posting API fetch + retry
│   ├── eightfold/client.py            # Eightfold Apply API fetch + retry
│   └── workday/client.py              # Workday candidate-site API fetch + retry
└── sheets/
    ├── writer.py                      # append new rows, prune closed postings
    ├── backfill.py                    # re-apply current classifiers to existing rows
    └── full_refresh.py                # rebuild and re-route every existing row
```

- **Role category**: `classify_roles.py` keyword-matches each job's title
  into one of `CATEGORIES` (or drops it as noise if nothing matches).
- **Region (PAC/RoW)**: `classify_region.py` buckets a job's location into
  `"PAC"` (Singapore/Hong Kong/Australia/Japan/South Korea/China/Taiwan/New
  Zealand) or `"RoW"`, so the target Singapore-based audience can
  find local roles without wading through everything else. Category ×
  region picks the tab.
- **Location normalization**: `normalize_location.py` cleans up each
  office name via a curated lookup table, reading each board's pre-split
  per-office field (Greenhouse's `offices`, Lever's `categories.allLocations`,
  Ashby's `location` + `secondaryLocations`, SmartRecruiters' single
  `location.city`, Eightfold's `locations[]`, or Workday's `locationsText`
  plus, for a multi-office posting, its detail endpoint's `location` +
  `additionalLocations`) rather than parsing an ambiguous free-text
  location string — see the module docstrings for why that field can't be
  safely comma-split. Eightfold's own entries are themselves hierarchical
  (`"City, Region, Country"`), so its client keeps only the leading city
  segment before handing it to the same lookup table every other board
  uses.
- **Seniority**: `classify_seniority.py` tags a best-effort level
  (Intern/Junior/.../Unspecified) off the title, computed once at fetch time.
- **Fetching**: `greenhouse/client.py` hits each board's public Job Board API
  (`content=true`); `lever/client.py` hits each site's public Postings API
  (`?mode=json`); `ashby/client.py` hits each board's public Posting API;
  `smartrecruiters/client.py` hits each company's public (paginated) Posting
  API; `eightfold/client.py` hits each company's own Eightfold Apply API
  (`<slug>.eightfold.ai`, paginated in fixed strides of 10 — the server's
  own hard page size, not a client-chosen one); `workday/client.py` POSTs
  to each company's own Workday careers-site search API
  (`<tenant>.<wdN>.myworkdayjobs.com`, 20 per page, the server's max). All
  six retry connection
  errors/timeouts/5xx/429 up to 3 times total (2s/4s backoff); other 4xx
  raise immediately. `main.py` logs and skips a board that still fails,
  rather than letting it trigger false pruning elsewhere. Neither Lever's,
  Ashby's, nor SmartRecruiters' API exposes an edited-at timestamp, so
  `updated_at` is always `""` for a job from any of them —
  `first_published` comes from Lever's `createdAt`/Ashby's
  `publishedAt`/SmartRecruiters' `releasedDate` instead. Eightfold is the
  exception: it exposes both `t_create` and `t_update` (Unix epoch
  seconds), so `first_published`/`updated_at` are both populated, same as
  Greenhouse. Workday gives no timestamp at all, only relative text
  ("Posted 3 Days Ago"), so its `first_published` is counted back from the
  scrape date — left `""` for "Posted 30+ Days Ago" — and `updated_at` is
  always `""`; a multi-office posting only shows "2 Locations" in the list,
  so its client makes one extra detail request for it, but only when its
  title matches a category (see `job_boards/workday/client.py`). Unlike
  Greenhouse/Lever/Ashby/SmartRecruiters, Eightfold's API is not documented/
  public at all — confirmed unreachable this way for most real Eightfold
  tenants tried, not just Millennium: of 7 real, verified Eightfold-hosted
  companies tried, only 2 (Millennium and Albemarle) were actually
  reachable; Morgan Stanley, Micron, Eaton, and Vodafone all 403'd even
  from their own live subdomain — see `job_boards/eightfold/client.py`'s
  module docstring.
- **Job description**: kept in the sheet (`description` column), but only
  for a firm in `models.FIRMS_NEEDING_DESCRIPTION` (currently QRT and
  Squarepoint) — every other posting's `description` is
  `""`. This is a deliberate trial scoped to two firms confirmed to have an
  apply page that doesn't reliably show the JD at all (one Greenhouse board
  renders it only into a client-side hydration blob, invisible/unsearchable
  on the page; another's `absolute_url` redirects to a generic careers
  listing instead of the specific posting), not a blanket rollout — most
  employers' own apply pages already show the JD fine. Where it is
  populated: Greenhouse's `content` field is HTML and gets run through
  `models.strip_html`; Lever's and Ashby's APIs already hand back plain
  text, so their clients use that directly. SmartRecruiters' client never
  populates it at all — the full JD isn't in its list endpoint. Eightfold's
  client gates it the same way Greenhouse/Lever/Ashby do, but its own list
  endpoint's `job_description` field is always "" on every live posting
  checked (unconfirmed whether Eightfold even has a separate per-posting
  detail endpoint with real JD text), and no live Eightfold firm is in
  `FIRMS_NEEDING_DESCRIPTION` today anyway, so in practice it's `""` there
  too. After any change to description extraction, its firm allowlist,
  formatting, or column position, run `sheets.full_refresh` (or its manual
  GitHub Actions workflow) to update existing rows; the daily scrape only
  adds new postings.
- **Salary**: the `salary` column follows the Apply link and precedes
  `department`. Lever and Ashby supply a human-readable range when their
  API response discloses one; otherwise every portal (including Workday)
  returns `None`, which is written to Google Sheets as a blank cell.
- **Identity**: a posting is uniquely identified by `(board, job_id)`, not
  `job_id` alone — every board assigns ids per tenant, so two different
  firms' boards can (and do) reuse the same id. `board` is the firm's
  readable name from `company_toml/greenhouse_boards.toml`/
  `company_toml/lever_boards.toml`/`company_toml/ashby_boards.toml`/
  `company_toml/smartrecruiters_boards.toml`/
  `company_toml/eightfold_boards.toml`/`company_toml/workday_boards.toml`
  (not the board token/slug), and `job_id` is that board's own id for the
  posting, kept as a string (Greenhouse's, SmartRecruiters', and
  Eightfold's are numeric, Lever's and Ashby's are UUIDs, Workday's is the
  requisition id from the posting's URL, e.g. `R-287924-1`).
- **Dedup/pruning**: keyed on that same `(board, job_id)` pair — a run
  appends a row the first time it sees one, never re-appends it while
  still open, and deletes it once it's gone from that board's feed, via one
  atomic batch of row deletions so a failure leaves the sheet untouched.
  Multi-token firms are only pruned once all their tokens succeed. Row
  order is never touched, so your own sort/filter setup survives every run.
- **Backfill**: `sheets/backfill.py` re-applies current classifiers,
  location normalization, and column order to every existing row — run it
  after changing `FIELDS`, keyword lists, the location lookup table, or
  `models.FIRMS_NEEDING_DESCRIPTION`. It also re-fetches every board to
  refresh `location`/`first_published`/`salary` for still-open postings, and
  recomputes `description` from `FIRMS_NEEDING_DESCRIPTION` — filling it
  in (and overwriting even a non-empty stale value, since a JD can be
  legitimately edited) for a firm in that set, blanking it back to `""`
  for every other firm. It deliberately doesn't move rows between tabs or
  rename board values, which could lose data if interrupted. It also
  (re-)applies date-column
  formatting, clips (rather than wraps) the `description` column so a long
  JD doesn't blow up its row's height, and turns on a basic filter
  (dropdown filter/sort arrows on every column, so
  board/location/seniority/company_type can be filtered) to each tab, at
  whatever column letters `FIELDS`' current order puts them.
- **Telegram digest**: `telegram.py` sends one optional message per run —
  this run's newly appended DA/BI, DS/ML/Algos, and SWE/DE postings, PAC
  only, grouped into count lines per (category, firm, location) rather than
  listed job-by-job, with one link back to the spreadsheet at the end
  instead of a per-job apply link. `main.py` gets the actual jobs (not just
  counts) from `sheets.writer.append_jobs`'s return value, to group them,
  plus the spreadsheet's own url for that trailing link. Unset
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` disables it entirely; a send
  failure is logged, never raised, so it can't fail an otherwise-successful
  run.

See `CLAUDE.md` for design rationale and edge-case invariants.

## Scale limits

This design — one GitHub Actions run, boards fetched one at a time, a
Google Sheet as the datastore — works fine at today's ~90 boards (54
Greenhouse, 6 Lever, 24 Ashby, 3 SmartRecruiters, 1 Eightfold, 2 Workday). Realistic
ceiling: **somewhere around 2,000-5,000 companies**, not the 10,000+ the
raw minutes math alone would suggest:

- **GitHub Actions minutes, idealized**: fetching is sequential (see
  `job_boards/greenhouse/client.py`, `job_boards/lever/client.py`,
  `job_boards/ashby/client.py`, `job_boards/smartrecruiters/client.py`,
  `job_boards/eightfold/client.py`, and `job_boards/workday/client.py`),
  ~0.25s/board observed (72 boards ≈ 18s
  total). This repo is a private GitHub repo, so its free tier is 2,000
  Actions minutes/month, shared
  across every private repo on the account. At 0.25s/board with negligible
  fixed overhead, that budget alone isn't exhausted until roughly
  **~15,000 companies** — higher than you'd guess.
- **...but that's optimistic.** Retries/backoff on transient failures
  (2s/4s each) aren't in that math, and at real scale some % of boards
  fail transiently every run. `get_all_values()` also re-reads each tab's
  *entire* accumulated history every run, so runtime grows with total
  tracked postings over time, not just company count that day. An
  Eightfold-hosted firm compounds this on its own: its API ignores the
  requested page size and always returns 10 postings per page, so a firm
  with ~230 open postings (Millennium's own count) costs ~23 requests
  where a similarly-sized Greenhouse/Ashby board costs 1-3 — this board
  type doesn't scale to many companies the way the other four do. Workday
  is worse still: 20 postings per page at ~2s per request, so Mastercard's
  ~1,050 postings alone take ~53 requests and ~2 minutes, plus a ~1-4s
  detail request per multi-office posting that matches a category.
- **The real unknown: the board platforms themselves.** This design hits
  thousands of different Greenhouse-, Lever-, Ashby-, SmartRecruiters-,
  Eightfold-, and Workday-hosted board subdomains sequentially from one CI runner IP.
  Each board is a different tenant, but each platform's own infrastructure
  is likely shared at the network/WAF layer — untested whether/when that
  starts looking like abusive traffic and gets throttled, independent of
  anything on this side. Eightfold specifically is a bigger unknown than
  the other four here (see the Fetching section above for its confirmed
  per-tenant lockdown rate).
- **Sheets' own hard cap**: a spreadsheet holds at most 10,000,000 cells
  (~770,000 rows at `FIELDS`' 14 columns). Where this binds depends on
  postings-per-company; today's sample (large quant/trading firms, ~45
  classified postings/board average) would hit it well under 10,000
  companies, but a broader, smaller-company mix would likely push this
  ceiling out past 10,000.

Past a few hundred boards, this needs concurrent/batched fetching, a real
database instead of a spreadsheet, and a dedicated scheduler/worker instead
of a GitHub Actions cron.

## Adding a board

```toml
# company_toml/greenhouse_boards.toml
[[boards]]
token = "stripe"          # slug from boards.greenhouse.io/<token>
firm = "Stripe"           # readable name, shown in the board column
company_type = "Big Tech" # free-form label, no fixed list
```

```toml
# company_toml/lever_boards.toml
[[boards]]
token = "palantir"                    # slug from jobs.lever.co/<token>
firm = "Palantir"                     # readable name, shown in the board column
company_type = "Data/Cloud Infrastructure" # free-form label, no fixed list
```

```toml
# company_toml/ashby_boards.toml
[[boards]]
token = "openai"          # slug from jobs.ashbyhq.com/<token>
firm = "OpenAI"            # readable name, shown in the board column
company_type = "AI Lab"   # free-form label, no fixed list
```

```toml
# company_toml/smartrecruiters_boards.toml
[[boards]]
token = "Grab"                       # identifier from jobs.smartrecruiters.com/<token>
firm = "Grab"                        # readable name, shown in the board column
company_type = "Consumer/Marketplace" # free-form label, no fixed list
```

```toml
# company_toml/eightfold_boards.toml
[[boards]]
token = "mlp"                          # subdomain from <token>.eightfold.ai
firm = "Millennium"                    # readable name, shown in the board column
company_type = "Prop Trading/Hedge Fund" # free-form label, no fixed list
```

Eightfold needs real per-firm verification before adding it here — unlike
Greenhouse/Lever/Ashby/SmartRecruiters, it has no documented public API and only one live
deployment has been confirmed to work; most real Eightfold tenants block
this query pattern outright. See `job_boards/eightfold/client.py`'s module
docstring before adding a second Eightfold-hosted firm.

```toml
# company_toml/workday_boards.toml
[[boards]]
token = "visa.wd5/Visa"   # <tenant>.<wdN>/<site> from <tenant>.<wdN>.myworkdayjobs.com/<site>
firm = "Visa"             # readable name, shown in the board column
company_type = "Fintech"  # free-form label, no fixed list
```

Workday's API isn't documented either, and the `wdN` data-center number
varies per company (Visa is `wd5`, Mastercard `wd1`), so take the token
from the company's real careers-site URL rather than guessing it.

Commit and push — all six files are checked in and read directly by local
runs and CI, no other config changes needed.

## Cron / CI

Two GitHub Actions workflows, both sharing
`concurrency: { group: sheet-write, cancel-in-progress: false }` so they
queue rather than run at the same time (both read-then-write every tab):

- **`.github/workflows/daily-scrape.yml`** ("Daily Scrape (Append)") runs
  `jobscraper.main` at `0 23 * * *` UTC (7am Singapore time, next day), plus manual
  `workflow_dispatch`. Appends newly seen jobs and prunes closed ones —
  the routine, incremental run.
- **`.github/workflows/full-refresh.yml`** ("Full Refresh (Rebuild)") runs
  `jobscraper.sheets.full_refresh` on manual `workflow_dispatch` only, no
  schedule. Recomputes every column and re-routes rows across tabs — run
  this by hand after a classification/metadata change has piled up enough
  stale or misplaced rows to be worth a full rebuild (see
  `sheets/full_refresh.py`'s module docstring).

Both need the same two repo secrets: `GOOGLE_SHEET_ID`,
`GOOGLE_SERVICE_ACCOUNT_JSON`. The daily scrape optionally also uses
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID` for the
Telegram digest — omitted (left as unset repo secrets) keeps that feature
disabled, same as leaving them unset in `.env` locally. The full refresh
doesn't send a digest, so it doesn't use the Telegram secrets.
