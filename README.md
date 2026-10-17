# latest-job-scraper

Fetches postings from Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold,
Workday, and Recruitee. Jobs are classified into Google Sheets tabs by role
and region, with optional Telegram notifications.
Citadel's browser-based SG/HK integration is disabled pending a Cloudflare
access fix; follow-up work is tracked in the integration backlog below.

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
uv run jobscraper-validate-boards                     # validate static board configuration
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
  DA/BI, SWE/DE, QT/QR, or SRE/DevOps. Generic engineering titles (SWE/DE),
  bare research titles at trading firms (QT/QR) and AI labs (DS/ML/Algos),
  and analytics titles (DA/BI) are fallbacks. Unmatched titles are omitted.
  Seniority uses title and department, with Greenhouse employment-type
  metadata as a fallback.
- **Regions:** PAC includes Singapore, Hong Kong, Australia, Japan, South
  Korea, China, Taiwan, New Zealand, Thailand, and roles listed for all of
  APAC or APJ. Jobs whose every office
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

Daily Scrape refetches each failed board once more, 60 seconds after the
first pass. Provider glitches such as Eightfold returning a duplicate ID
across pages can clear within minutes. A board that fails again is
excluded from pruning. All configured tokens for a firm
must succeed before its rows can be removed. A failed Workday detail request
fails the board, except a confirmed 404/410 for a closed posting. Workday
also lists items with neither a title nor a link, such as
`{"bulletFields": ["REF083382W"]}`, that no requisition search finds. These
are counted toward the total and then skipped. More than three per board,
and more than 5% of its listing, fails the board instead.

Before any job rows are appended, the prune guard checks each firm's rows
across all tabs. It holds back a firm if its feed is empty, or if at least
five rows and more than half its tracked rows would be deleted. Both appends
and deletions for that firm are held back, keeping the baseline stable on
retries. Other firms continue, and the run fails after reporting the hold.

If the closures are confirmed, rerun Daily Scrape with `force_prune_firms`
set to those firm names, comma-separated. Locally, use `FORCE_PRUNE_FIRMS`
for that run only. Use Full Refresh for intentional routing changes.
A board that keeps returning 0 postings may have moved to another provider;
probe the other provider APIs before forcing.

The run summary and the final error name each failed board's error and
each held-back firm's reason.

Sheets requests retry quota rejections (HTTP 429) after 10, 20, 40 and 60
seconds. Reads also retry 5xx responses, including HTML error pages, and
dropped or timed-out connections after 5, 15 and 30 seconds. Writes are not
retried after those failures, because an append may already have landed.

Daily pruning is atomic per tab. A daily run can still partially complete
across tabs. Full Refresh commits all job rows and grid sizes in one atomic
Sheets batch, then applies formatting. A formatting failure can leave the
new data committed and requires rerunning the refresh. Existing basic-filter
criteria and sort settings are retained when ranges are updated.

## Refreshing existing data

**Backfill** refreshes title, department, seniority, company type, source,
location, updated and first-published dates, salary, allowlisted
descriptions, date formatting, the frozen header row and URL formulas. It
rewrites columns in the current schema, including on header-only tabs. It
preserves the URL target and scrape date. Rows whose posting has closed, or
whose board couldn't be fetched, keep their existing text and fall back to
local classification/normalization. Salary may become blank when a fresh
response no longer supplies it.

**Full Refresh** performs the same recomputation and also routes rows under
the current category and region rules, using each open posting's current
title. It deduplicates identities across tabs and removes rows the daily
scrape would no longer route: titles that match no category and jobs wholly
in excluded countries. Left in place, the next daily run would prune them
as closures. It also adds open postings that route but aren't tracked, such
as ones a new keyword matches, without notifications. A posting first
listed shortly before a Full Refresh is therefore added silently. It does
not remove a posting solely because its board no longer lists it.

Run the appropriate refresh after deploying changes to classifications,
location mappings, descriptions, schema, or formatting. Use Full Refresh
before the next daily scrape for routing changes, to preserve original
`scraped_date` values and to avoid announcing moved rows, or long-open
postings a change newly matches, as new.

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
uv run jobscraper-validate-boards
uv run jobscraper-validate-boards --live --provider greenhouse --firm Example
ONLY_FIRMS=Example uv run python -m jobscraper.board_health
```

Static validation rejects missing or unknown fields, surrounding whitespace,
malformed provider tokens, duplicate tokens, and inconsistent metadata for a
firm configured more than once. `--live` also verifies endpoint access and
unique job IDs; an empty result fails unless `--allow-empty` is explicit.
The board-health command remains the concurrent operational check used by CI.

`ONLY_FIRMS` scopes health checks and daily job fetching. It does not scope
the Supported companies reference tab. Wheels bundle the board files and
must be rebuilt after configuration changes.

### Integration backlog

Dedicated integrations are still needed for the tracked backlog: Gojek,
Shopify, Zoom, Meta, Amazon, Google, The Trade Desk, Apple, and
Atlassian. X is configured through SmartRecruiters.

- [ ] Revisit Citadel SG/HK browser access. On 2026-10-02, both the GitHub
  Actions probe and a local headed Chromium attempt with manual challenge
  clicks failed to clear Cloudflare. Compare access in an ordinary browser
  with the automated session, resolve the access blocker, and verify the
  parser against captured live listing HTML. Keep
  `company_toml/citadel_boards.toml` at `boards = []` until the probe passes
  consistently and scoped live validation succeeds. The scheduled Actions
  probe was removed on 2026-10-17 after failing daily at the Cloudflare
  challenge. Deferred; no activation planned until this work is revisited.

### Citadel browser probe

Citadel is registered but disabled (`boards = []` in
`company_toml/citadel_boards.toml`). The read-only probe needs no Sheets or
Telegram credentials. It uses the production fetcher and reports
completeness, global/SG/HK counts, matching role counts, elapsed time and the
failure reason. With `--diagnostics-dir`, it also writes a failure
screenshot; cookies and browser profiles are never saved.

To run it locally:

```bash
uv run playwright install chromium --with-deps --no-shell
uv run jobscraper-citadel-probe --output /tmp/citadel-probe.json --diagnostics-dir /tmp/citadel-probe
```

The fetch uses one modern headless Chromium context, allows automatic
Cloudflare verification, and fails if an interactive challenge persists for
45 seconds. Listing navigations are separated by at least ten seconds. One
whole-snapshot retry is allowed for transient navigation or incomplete
pagination, within a five-minute fetch deadline. A failed fetch cannot prune
Citadel rows. Chromium is installed conditionally in other fetching workflows,
so disabled or unselected Citadel boards need no browser setup.

All global pages must match their displayed counts and contain valid unique
identities before the fetcher retains postings with any Singapore or Hong
Kong office. Every office on retained jobs is preserved. Unknown office
labels or HTML changes fail the board rather than returning partial data.
The structural HTML parser must be verified by a successful live probe; local
Chromium checks currently stop at Cloudflare, so parser fixtures represent
the expected structure rather than captured live HTML.

Enable Citadel only after the probe passes consistently, each pass with at
least one SG/HK posting matching the tracked role categories. The probe
reports readiness only; it does not edit configuration or write to Sheets.
To enable it, replace the empty board list with:

```toml
[[boards]]
token = "citadel"
firm = "Citadel"
company_type = "Prop Trading/Hedge Fund"
```

Then run static validation and scoped live validation before enabling daily
collection. Detail-page URL slugs identify postings, so a repost using the
same slug is not treated as a new job. Publication/update dates, department,
salary and descriptions remain blank; no detail pages are fetched.
Citadel Securities is a separate, unsupported source. For a manual headed
diagnostic, use `xvfb-run uv run jobscraper-citadel-probe --headed`; headed
mode is not an automatic fallback and does not guarantee clearance.

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
A push that only removes entries selects no firms and skips the scrape, as
PR validation skips fetches. Both compare parsed TOML.

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

## Future work

- [ ] Eventually migrate the Google Sheets job board to a searchable,
  mobile-friendly website hosted on the existing Hetzner server. Gate access
  like the current sheet: require sign-in, restrict access to explicitly
  approved users, and allow access to be revoked. Protect both job pages and
  data endpoints. Reuse the scraper, classifications, closure safeguards,
  and Telegram alerts; migrate existing jobs while preserving first-seen
  dates and avoiding duplicate new-job notifications. Plan database storage,
  scheduled scraping, HTTPS, and backups when this work is taken up.
