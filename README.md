# latest-job-scraper

Collects open postings from company career boards into a Google Sheet, filed by role and region, with optional Telegram alerts. The sheet is its only memory.

Each run: fetch → validate → classify → append/prune → notify.

- Why it works this way, and what happens on failure: [blog.md](blog.md)
- How roles, regions and locations are decided: [docs/rules.md](docs/rules.md)
- Rules for working on the code: [CLAUDE.md](CLAUDE.md)

## What you get

Each posting is one row on a tab named `"{category} - {region}"`, such as `SWE/DE - PAC`. This is an invented example:

| Column | Example | Meaning |
| --- | --- | --- |
| `first_published` | 2026-11-03 | Date the provider published it; blank if unknown |
| `updated_at` | 2026-11-10 | Date the provider last updated it |
| `board` | Example | The firm name, part of the posting's identity |
| `title` | Senior Data Engineer | |
| `location` | Singapore | Offices joined with ` \| ` |
| `seniority` | Senior | `Unspecified` when no level matches |
| `company_type` | Fintech | Set per firm in `company_toml/` |
| `url` | Apply | A `HYPERLINK` formula to the posting |
| `salary` | | Lever and Ashby only |
| `department` | Data Platform | |
| `job_id` | 4821937 | The provider's ID, unique within the firm |
| `scraped_date` | 2026-11-12 | Date the scraper first saw it |
| `source` | Greenhouse | The provider |
| `description` | | Only for allowlisted firms |

A Telegram digest of new PAC roles looks like this:

```text
📦 3 new PAC role(s)

SWE/DE
Example x2 (Singapore)
Other Firm x1 (Hong Kong)

See them in the sheet
```

## Terms

- **Board:** one firm's careers feed on one provider, configured by an entry in `company_toml/`.
- **Firm:** the display name in that entry. It is stored in the `board` column.
- **Posting identity:** `(firm, job_id)`. This is how the scraper knows it has seen a posting.
- **Tab:** one sheet per role category and region, such as `SWE/DE - PAC`.
- **PAC / RoW:** Asia-Pacific, and the rest of the world.
- **Noise:** a posting whose title matches no role. It is dropped.
- **Prune:** deleting rows whose posting is no longer listed. Closed postings are deleted, not marked.
- **Prune guard:** holds back a firm whose feed is empty or has dropped sharply. See [blog.md](blog.md).
- **Backfill:** refreshes existing rows in place and never moves them.
- **Full Refresh:** refreshes every row, then moves rows between tabs by the current rules.
- **Supported companies:** a reference tab listing every firm. It is created automatically.

## Job boards

- Greenhouse
- Lever
- Ashby
- SmartRecruiters
- Eightfold
- Recruitee
- Amazon (amazon.jobs)
- Apple (jobs.apple.com)
- TikTok (lifeattiktok.com)

Firms and tokens live in [`company_toml/`](company_toml/).

Not supported: Citadel, or any other board behind a Cloudflare challenge. They are not worth the time ([docs/cloudflare-challenges.md](docs/cloudflare-challenges.md)).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
cp .env.example .env
```

1. Enable the Sheets API, create a service account, and share the spreadsheet with its `client_email` as Editor.
2. In `.env`, set `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` (the whole key on one line).
   - Optional `GOOGLE_SHEET_TITLE` makes every run refuse a spreadsheet with a different title.
3. Create ten tabs named `"{category} - {region}"`:
   - Categories: DS/ML/Algos, DA/BI, SWE/DE, QT/QR, SRE/DevOps.
   - Regions: PAC, RoW.
   - Example: `SWE/DE - PAC`.
4. Give each tab the header from the table above, in this order ([`models.FIELDS`](src/jobscraper/models.py)):
   - `first_published, updated_at, board, title, location, seniority, company_type, url, salary, department, job_id, scraped_date, source, description`
   - Backfill or Full Refresh can write it to an empty tab.

The **Supported companies** tab only adds or recategorizes firms and never drops one. An empty tab is filled without announcements.

Keep credentials in `.env` or Actions secrets. Board tokens are public config in `company_toml/`.

## Commands

```bash
uv run pytest
uv run jobscraper-validate-boards                       # static board checks
uv run python -m jobscraper.board_health                # fetch boards; no sheet access
uv run python -m jobscraper.main                        # daily run
uv run python -m jobscraper.sheets.backfill             # refresh rows in place
uv run python -m jobscraper.sheets.full_refresh         # refresh and move rows between tabs
uv run python -m jobscraper.sheets.supported_companies  # sync firms, announce
uv run python -m jobscraper.sheets.audit                # read-only sheet audit
uv run python -m jobscraper.telegram --check            # test Telegram destinations
uv run python -m jobscraper.alerts --check              # test the DM, plus one live sample
```

Tests block unmocked HTTP and need no credentials.

Two environment variables narrow or override a run:

- `ONLY_FIRMS` (comma-separated) limits board health and the daily run. It doesn't limit Backfill, Full Refresh or the Supported companies tab.
- `FORCE_PRUNE_FIRMS` overrides the prune guard for confirmed closures. See [blog.md](blog.md).

### Before writing to the production sheet

- Check that `git status` is clean.
- Fetch, then check that `git log origin/main..HEAD` is empty. The checkout must match what is pushed.
- Use `main`. Scheduled runs use it, and local schema changes must use the same code. When dispatching an Actions workflow, select `main` too.
- Don't overlap writers. Local runs share no lock with each other or with Actions.

## Which refresh to run

| You changed | Run | Why |
| --- | --- | --- |
| Seniority rules only | Backfill | Rewrites in place; never moves rows. |
| Role keywords, region or country rules, location mapping | Full Refresh, before the next daily run | Moves rows, keeps first-seen dates, no re-announcements. |
| Descriptions, allowlist, salary, column layout, formatting | Full Refresh | Project rule. Backfill also rewrites these, but tab by tab. |

Backfill:

- Clears descriptions of firms not on the allowlist.
- Blanks salary a live posting no longer has.
- Leaves rows whose posting closed, or whose board failed, as they were.

Full Refresh does the same, then:

- Routes rows by the current rules.
- Removes cross-tab duplicates.
- Drops rows the daily run would no longer route.
- Silently adds open postings that now route and aren't tracked yet.
- Never deletes a row only because its board stopped listing it.

## Rules in brief

The full rules are in [docs/rules.md](docs/rules.md).

- **Roles:** the first matching title keyword wins: DS/ML/Algos, DA/BI, SWE/DE, QT/QR, SRE/DevOps. An unmatched title is noise.
- **Seniority:** from the title and department, then Greenhouse's employment type.
- **Regions:** one PAC office makes a posting PAC. It is dropped only if every office is in an excluded country. Everything else is RoW.
- **Dates:** `YYYY-MM-DD`, blank when the provider gives none.
- **Descriptions and salary:** descriptions only for allowlisted firms; salary from Lever and Ashby only.
- **Text safety:** external text stays literal. Only the `url` column is a formula.

## Telegram

Each destination is optional. All need `TELEGRAM_BOT_TOKEN` plus their own chat ID.

| Message | Required | Optional topic |
| --- | --- | --- |
| New PAC DA/BI, DS/ML/Algos and SWE/DE postings, counted by firm and location | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |
| Newly added firms, by company type | `TELEGRAM_COMPANIES_CHAT_ID` | `TELEGRAM_COMPANIES_THREAD_ID` |
| One message per new SG/HK data-engineering posting | `TELEGRAM_DM_CHAT_ID` | none |

For the DM:

- The recipient must press Start on the bot first.
- Each message has an apply link, plus a description only for an allowlisted firm.
- At most 15 go out per run, then an overflow notice.

## Adding or changing a board

Add an entry to `company_toml/<provider>_boards.toml`. The provider is one of Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, Recruitee, Amazon, Apple or TikTok.

```toml
[[boards]]
token = "example"
firm = "Example"
company_type = "Fintech"
```

- Take the token from the firm's real careers site.
- A firm in two files must use the same spelling and `company_type`.
- **Amazon:** tokens are amazon.jobs category slugs, such as `"software-development"`, all under the firm `Amazon`.
  - A slug the site doesn't recognise fails the board.
  - A recognised category may be empty.
  - A category with 10,000 or more postings fails on its first page, because the endpoint stops there.
- **Apple:** the token lists jobs.apple.com team codes, separated by commas. A code can name one sub-team after a slash: `"SFTWR,MLAI,STDNT/INTRN"`.
  - Keep all of Apple in one board. A posting listed under two teams comes back once; two boards would fetch it twice under one ID.
  - A team or sub-team the site doesn't list fails the board, and so does an empty search.
  - The codes are listed at `https://jobs.apple.com/api/v1/refData/teamsofinterest`.
- **TikTok:** the token lists lifeattiktok.com city codes, separated by commas. `"CT_163"` is Singapore.
  - Only postings in the listed cities are kept.
  - One request returns a city's whole listing. A response shorter than its own count fails the board.
- **Eightfold, Amazon, Apple and TikTok:** the endpoints are undocumented. Validate each tenant, category, team or city first.

Then check it:

```bash
uv run jobscraper-validate-boards
uv run jobscraper-validate-boards --live --provider greenhouse --firm Example
ONLY_FIRMS=Example uv run python -m jobscraper.board_health
```

- Static validation rejects unknown or missing fields, stray whitespace, bad or duplicate tokens and inconsistent firm metadata.
- `--live` also fetches the board. An empty one fails unless you pass `--allow-empty`.
- The built package bundles board files, so rebuild after changes.

Removing a board leaves its rows and Supported companies entry behind, because nothing prunes a firm that is no longer fetched.

**Do not rename a `firm` casually.** It is part of every row's identity. See [Known gap](blog.md#known-gap-renaming-a-firm).

## GitHub Actions

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| Tests | Push to main, PRs | Validate config, run `pytest` |
| Validate Changed Boards (PR) | PR touching board TOML | Fetch changed entries |
| Board Health Check | Sundays 12:00 UTC, manual | Fetch every board |
| Daily Scrape (Append) | Board TOML push to main, 23:00 UTC daily, manual | Scrape, notify, audit |
| Backfill (Refresh Existing Rows) | Manual | Refresh in place, audit |
| Full Refresh (Rebuild) | Manual | Refresh and reroute, audit |
| Sync Supported Companies | Manual | Sync firms, announce additions |
| Telegram DM Check | Manual | Test DM, live sample |

Daily Scrape:

- 23:00 UTC is 07:00 Singapore time the next day.
- On a TOML push it fetches only added or modified firms. A removal-only push scrapes nothing.
- A manual run takes `force_prune_firms`.

The four sheet writers (Daily Scrape, Backfill, Full Refresh and Sync Supported Companies):

- Run `pytest` first.
- Share the `sheet-write` concurrency group, so Actions never overlaps them.

The audit:

- Runs after the first three writers, even after a failure, and keeps `data-quality.json` for 14 days.
- Fails on broken keys or headers.
- Treats blank metadata and cross-tab duplicates as advisory.
- Repairs nothing.

## Backlog

- Dedicated integrations (X already runs through SmartRecruiters). Endpoint notes for Google: [docs/google-apple.md](docs/google-apple.md).
  - Gojek
  - Shopify
  - Zoom
  - Meta
  - Google
  - The Trade Desk
  - Atlassian
- A lock shared by local and Actions writers.
- Numeric seniority such as Engineer II.
- A searchable, mobile-friendly site on the Hetzner server, replacing the sheet:
  - Sign-in, approved users, and revocable access on pages and data.
  - First-seen dates kept, and no duplicate alerts.
  - Needs a database, scheduling, HTTPS and backups.
- Limits: Sheets quotas, full-tab reads, provider rate limits and the cell cap bound scale. No capacity is measured.
