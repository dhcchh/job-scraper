# latest-job-scraper

Fetches postings from Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold,
Workday, and Recruitee. Jobs are classified into Google Sheets tabs by role
and region, with optional Telegram notifications.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
cp .env.example .env
```

1. Enable the Google Sheets API, create a service account and download its
   JSON key. Share the target spreadsheet with the account's `client_email`
   as Editor.
2. Set `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env`. The
   latter is the complete JSON key on one line. Optional `GOOGLE_SHEET_TITLE`
   rejects a spreadsheet whose title differs from the configured value.
3. Create the ten job tabs: each category in
   [`classify_roles.py`](src/jobscraper/enrichment/classify_roles.py) paired
   with each region in
   [`classify_region.py`](src/jobscraper/enrichment/classify_region.py), named
   `"{category} - {region}"`, such as `"SWE/DE - PAC"`.
4. Give each job tab the header from
   [`models.FIELDS`](src/jobscraper/models.py), in that exact order:
   `first_published, updated_at, board, title, location, seniority, company_type, url, salary, department, job_id, scraped_date, source, description`.
   Daily scraping rejects missing or mismatched headers. Backfill can
   initialize or migrate headers on existing empty tabs.

The separate **Supported companies** tab is created automatically if absent.
Board tokens are public configuration in `company_toml/*_boards.toml`.
Credentials belong in `.env` or GitHub Actions secrets.

## Commands

```bash
uv run pytest
uv run python -m jobscraper.board_health             # fetch boards; no sheet credentials needed
uv run python -m jobscraper.main                     # append new jobs and prune closures
uv run python -m jobscraper.sheets.backfill          # refresh existing rows in place
uv run python -m jobscraper.sheets.full_refresh      # refresh and move rows between tabs
uv run python -m jobscraper.sheets.supported_companies # sync firms and announce additions
uv run python -m jobscraper.sheets.audit             # read-only sheet audit
uv run python -m jobscraper.telegram --check         # test configured Telegram destinations
uv run python -m jobscraper.alerts --check           # test the DM destination with a live sample
```

Before a local production write, use a clean checkout committed and pushed
to `origin/main`, as described in [CLAUDE.md](CLAUDE.md). Tests replace local
integration settings, disable dotenv loading, and block unmocked HTTP requests.

## Data behavior

- **Identity:** `(board, job_id)` identifies a posting. `board` is the firm
  name from TOML; IDs are strings. `scraped_date` records when a row was
  first added. Daily runs leave metadata on existing rows unchanged.
- **Roles:** title keywords select the first matching category: DS/ML/Algos,
  DA/BI, SWE/DE, QT/QR, or SRE/DevOps. Unmatched titles are omitted.
  Seniority uses title and department, with Greenhouse employment-type
  metadata as a fallback.
- **Regions:** PAC includes Singapore, Hong Kong, Australia, Japan, South
  Korea, China, Taiwan, New Zealand, and Thailand. Jobs whose every office
  is in Vietnam, Malaysia, Indonesia, India, Armenia, or Uruguay are
  excluded. Other locations go to RoW. A posting with any PAC office goes
  to PAC unless excluded.
- **Locations:** normalize each provider's office separately, deduplicate,
  and join with ` | `. Free-text lists are not comma-split. Eightfold's
  hierarchical locations retain their country; SmartRecruiters' separate
  country field is retained, as is Recruitee's. Unresolved offices in newly
  appended rows appear in the coverage report.
- **Dates:** timestamps become `YYYY-MM-DD`. Fields unavailable from an API
  remain blank. Workday relative dates use a date inferred from one posting's
  exact start date, falling back to the scrape date; ambiguous "30+ days"
  values remain blank unless an exact detail date is available.
- **Descriptions:** only QRT and Squarepoint currently retain full job
  descriptions. The allowlist is `FIRMS_NEEDING_DESCRIPTION` in `models.py`.
- **Salary:** current extraction covers Lever and Ashby. Greenhouse also
  supports structured pay ranges through its job-detail endpoint with
  `pay_transparency=true`; this client does not fetch details for salary.
  See the [Greenhouse API](https://docs.greenhouse.io/job-board.html#retrieve-a-job).
  Other clients currently leave salary blank.
- **Literal text:** external text is protected from formula interpretation;
  the URL column contains generated `HYPERLINK` formulas.

## Failures and pruning

Board requests retry connection failures, timeouts, HTTP 429 and 5xx up to
three attempts, with 2s/4s backoff, or a 429's `Retry-After` seconds (capped
at 60s). Other HTTP errors fail immediately.
Missing or malformed job lists, invalid/missing IDs, and repeated IDs fail
the board. Paginated snapshots must reach the reported total with unique
identities; an incomplete snapshot is retried before rejection. Boards are
fetched concurrently (eight at a time, with Workday boards in a separate
pool of four) over shared keep-alive connections. Workday and Eightfold
fetch pages, and Workday required detail records, with up to eight workers.

A failed board is excluded from pruning. All configured tokens for a firm
must succeed before its rows can be removed. A failed Workday detail request
fails the board, except a confirmed 404/410 for a closed posting.

Before any job rows are appended, the prune guard checks each firm's rows
across all tabs. It holds back a firm if its feed is empty, or if at least
five rows and more than half its tracked rows would be deleted. Both appends
and deletions for that firm are held back, keeping the baseline stable on
retries. Other firms continue, and the run fails after reporting the hold.

If the closures are confirmed, rerun Daily Scrape with `force_prune_firms`
set to those firm names, comma-separated. Locally, use `FORCE_PRUNE_FIRMS`
for that run only. Use Full Refresh for intentional routing changes.

Daily pruning is atomic per tab. A daily run can still partially complete
across tabs. Full Refresh commits all job rows and grid sizes in one atomic
Sheets batch, then applies formatting. A formatting failure can leave the
new data committed and requires rerunning the refresh. Existing basic-filter
criteria and sort settings are retained when ranges are updated.

## Refreshing existing data

**Backfill** refreshes seniority, company type, source, location,
first-published date, salary, allowlisted descriptions, date formatting and
URL formulas. It rewrites columns in the current schema, including on
header-only tabs. It preserves title, department, URL target, updated date,
and scrape date. Unavailable board overrides fall back to existing values
and local classification/normalization. Salary may become blank when a
fresh response no longer supplies it.

**Full Refresh** performs the same recomputation and also routes rows under
the current category and region rules. It deduplicates identities across
tabs and removes jobs wholly in excluded countries. Unmatched titles remain
on their current tab. It does not discover new jobs or remove a posting
solely because its board no longer lists it.

Run the appropriate refresh after deploying changes to classifications,
location mappings, descriptions, schema, or formatting. Use Full Refresh
before the next daily scrape for routing changes, to preserve original
`scraped_date` values and avoid treating moved rows as new notifications.

Supported-company sync adds configured firms, moves recategorized firms,
and retains firms removed from configuration until manually removed from
that tab. An initially empty tab does not announce the entire company list.

## Telegram

All destinations use `TELEGRAM_BOT_TOKEN`. Each destination is optional:

| Message | Required destination | Optional topic |
| --- | --- | --- |
| Grouped counts of new PAC DA/BI, DS/ML/Algos and SWE/DE jobs | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |
| Newly added firms, grouped by company type | `TELEGRAM_COMPANIES_CHAT_ID` | `TELEGRAM_COMPANIES_THREAD_ID` |
| Individual new SG/HK data-engineering roles | `TELEGRAM_DM_CHAT_ID` | None |

The DM recipient must start the bot first. DMs include an apply link and
include a description only for an allowlisted firm. They are capped at 15
postings per run, followed by an overflow notice. Long messages may be split.

Notifications are best effort. Failed sends are logged and are not queued
for retry on later runs. Company announcements use the reference tab as
the record of previously seen firms.

## Adding or changing boards

Add an entry to the matching provider's TOML file:

```toml
[[boards]]
token = "example"
firm = "Example"
company_type = "Fintech"
```

Use the token from the actual careers site. Workday uses
`"<tenant>.<wdN>/<site>"` and optionally `location_scope = "Singapore"` to
search and retain only matching offices. Eightfold and Workday use
undocumented endpoints; validate each tenant before relying on it.

```bash
ONLY_FIRMS=Example uv run python -m jobscraper.board_health
```

`ONLY_FIRMS` scopes health checks and daily job fetching. It does not scope
the Supported companies reference tab. Wheels bundle the board files and
must be rebuilt after configuration changes.

Dedicated integrations are still needed for the tracked backlog: Gojek,
Shopify, Zoom, Citadel, Meta, Amazon, Google, The Trade Desk, Apple, and
Atlassian. X is configured through SmartRecruiters.

## GitHub Actions

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| Tests | Push to main and PR | Run the test suite |
| Validate Changed Boards | PR changing board TOML | Fetch added or modified entries, including token/scope edits |
| Board Health Check | Weekly and manual | Fetch all configured boards |
| Daily Scrape | Board TOML push to main, daily at 23:00 UTC, manual | Sync firms, scrape, notify, audit |
| Backfill | Manual | Refresh rows in place and audit |
| Full Refresh | Manual | Refresh and reroute rows atomically, then audit |
| Sync Supported Companies | Manual | Sync the reference tab and announce additions |
| Telegram DM Check | Manual | Send a test DM and one matching live sample |

23:00 UTC is 07:00 Singapore time the next day; scheduled runs can be delayed.
On TOML pushes, Daily Scrape fetches firms with added or modified entries.
With no selected changes, it runs the full list. PR validation skips fetches
when no entries were added or modified. Both compare parsed TOML.

The four sheet-writing workflows share the `sheet-write` concurrency group.
This serializes active Actions runs, but does not protect against local
writers or guarantee every pending trigger runs. Sheet commands need Google
credentials; health checks and Telegram checks do not. Set optional Telegram
secrets only for the desired destinations.

Scrape, Backfill and Full Refresh run tests before writing, then audit even
after a command failure. The audit checks schemas, identities and duplicates;
blank metadata and duplicates across tabs are advisory. Reports appear in
the run summary and a JSON artifact retained for 14 days. The audit reports
issues without repairing data or rolling back completed writes.

Sheets quotas, full-tab reads, provider rate limits, and the spreadsheet
cell cap constrain scale. Runtime depends on provider pagination and job
volume; there is no measured company-count capacity guarantee.
