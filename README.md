# latest-job-scraper

Collects open postings from company career boards into a Google Sheet, filed by role and region, with optional Telegram alerts. The sheet is its only memory.
Each run: fetch → validate → classify → append/prune → notify. Decisions, failures and conflicts are in [blog.md](blog.md).
Job boards: Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, Workday, Recruitee and Amazon (amazon.jobs). Firms and tokens live in [`company_toml/`](company_toml/).
Citadel's SG/HK integration is disabled: [docs/citadel.md](docs/citadel.md).

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
cp .env.example .env
```

1. Enable the Sheets API, create a service account, and share the spreadsheet with its `client_email` as Editor.
2. In `.env`, set `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` (the whole key on one line). Optional `GOOGLE_SHEET_TITLE` makes every run refuse a spreadsheet with a different title.
3. Create ten tabs named `"{category} - {region}"`, such as `"SWE/DE - PAC"`. Categories: DS/ML/Algos, DA/BI, SWE/DE, QT/QR, SRE/DevOps ([`classify_roles.py`](src/jobscraper/enrichment/classify_roles.py)). Regions: PAC, RoW ([`classify_region.py`](src/jobscraper/enrichment/classify_region.py)).
4. Give each tab this header, in order ([`models.FIELDS`](src/jobscraper/models.py)): `first_published, updated_at, board, title, location, seniority, company_type, url, salary, department, job_id, scraped_date, source, description`. Backfill or Full Refresh can write it to an empty tab.

The **Supported companies** tab is created automatically. It only adds or recategorizes firms, never drops one, and an empty tab is filled without announcements. Keep credentials in `.env` or Actions secrets; board tokens are public config in `company_toml/`.

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

Before a local production write, the checkout must be clean and pushed to `origin/main` ([CLAUDE.md](CLAUDE.md)). Local runs share no lock, so never overlap writers. Tests block unmocked HTTP.

`ONLY_FIRMS` (comma-separated) limits board health and the daily run, not Backfill, Full Refresh or the Supported companies tab. `FORCE_PRUNE_FIRMS` is the prune-guard override in [blog.md](blog.md).

## Which refresh to run

| You changed | Run | Why |
| --- | --- | --- |
| Seniority rules only | Backfill | Rewrites in place; never moves rows. |
| Role keywords, region or country rules, location mapping | Full Refresh, before the next daily run | Moves rows, keeps first-seen dates, no re-announcements. |
| Descriptions, allowlist, salary, column layout, formatting | Full Refresh | Project rule. Backfill also rewrites these, but tab by tab. |

Backfill also clears descriptions of firms not on the allowlist, and blanks salary a live posting no longer has. Rows whose posting closed, or whose board failed, keep their text. Full Refresh does the same, then routes rows by current rules. It removes cross-tab duplicates, drops rows the daily run would no longer route, and silently adds open untracked postings that route. It never deletes a row only because its board stopped listing it.

## Rules in brief

- **Roles:** the first matching title keyword wins, in the order DS/ML/Algos, DA/BI, SWE/DE, QT/QR, SRE/DevOps. Fallbacks: generic senior engineering titles go to SWE/DE; bare "research" to QT/QR at `Prop Trading/Hedge Fund` firms and DS/ML/Algos at `AI Lab` firms; bare "analytics" to DA/BI. Recruiter, solutions-architect, legal-engineer, developer-relations, customer-facing engineering-lead and non-systematic trader titles are noise. A team-name keyword doesn't count on a product, program, project, technical-writer or service-delivery title ("Product Manager, Data Engineering"). Anything unmatched is noise, with one Amazon exception below. Amazon jobs alone get extra keywords for Amazon's own titles (SDE, SDM, Software Dev Engineer, Software Development, System Development Engineer, BIE, Applied Science, Research Science) and extra noise: every data-centre role, Infrastructure Reliability & Quality, QA and SDET, technicians, delivery consultants, physical security and program, project or product managers. An otherwise unmatched Amazon posting in the "Software Development" job family is SWE/DE, unless its title names a job that isn't software engineering: one of the noise titles above, a product owner, a customer-facing engineer, a user researcher, a designer or a writer. Seniority uses title and department, then Greenhouse's employment type.
- **Regions:** PAC is Singapore, Hong Kong, Australia, Japan, South Korea, China, Taiwan, New Zealand, Thailand, or a role listed for APAC or APJ; one PAC office is enough. A posting is excluded only if every office is in Vietnam, Malaysia, Indonesia, India, Armenia or Uruguay. All else is RoW.
- **Locations:** each office is normalized separately and joined with ` | `, keeping provider country data. A label splits on `/`, `;` or ` or ` only when every part names a known place; free text is never comma-split. GovTech uses its location label (its offices name teams). Unresolved offices in new rows appear in the run summary.
- **Dates:** `YYYY-MM-DD`; blank if the provider gives none. Workday's relative dates count back from one posting's exact start date, else the scrape date; "30+ days" stays blank unless a detail page gives a date.
- **Descriptions:** only for firms in `FIRMS_NEEDING_DESCRIPTION` (`models.py`; QRT and Squarepoint), and never from SmartRecruiters or Workday.
- **Salary:** Lever and Ashby only. Greenhouse offers ranges via its [job-detail endpoint](https://docs.greenhouse.io/job-board.html#retrieve-a-job) (`pay_transparency=true`), which this project does not call (per Greenhouse docs; not verified).
- **Text safety:** external text stays literal: text starting with `=`, `+`, `-` or `@` is escaped, and Full Refresh writes typed values. Only the `url` column, a generated `HYPERLINK` formula, is a formula.

## Telegram

Each destination is optional. All need `TELEGRAM_BOT_TOKEN` plus their own chat ID.

| Message | Required | Optional topic |
| --- | --- | --- |
| New PAC DA/BI, DS/ML/Algos and SWE/DE postings, counted by firm and location | `TELEGRAM_CHAT_ID` | `TELEGRAM_THREAD_ID` |
| Newly added firms, by company type | `TELEGRAM_COMPANIES_CHAT_ID` | `TELEGRAM_COMPANIES_THREAD_ID` |
| One message per new SG/HK data-engineering posting | `TELEGRAM_DM_CHAT_ID` | none |

The DM recipient must press Start on the bot first. A DM has an apply link, plus a description only for an allowlisted firm. At most 15 go out per run, then an overflow notice.

## Adding or changing a board

Add to `company_toml/<provider>_boards.toml` (Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, Workday, Recruitee or Amazon):

```toml
[[boards]]
token = "example"
firm = "Example"
company_type = "Fintech"
```

Take the token from the firm's real careers site. A firm in two files must use the same spelling and `company_type`. Workday tokens look like `"<tenant>.<wdN>/<site>"` and accept `location_scope = "Singapore"` to keep matching offices. Amazon tokens are amazon.jobs category slugs (`"software-development"`), all under the firm `Amazon`. A category the site doesn't know returns no postings and fails the board, and one with 10,000 or more postings fails as incomplete, because the endpoint stops there. Eightfold, Workday and Amazon endpoints are undocumented; validate each tenant or category first.

```bash
uv run jobscraper-validate-boards
uv run jobscraper-validate-boards --live --provider greenhouse --firm Example
ONLY_FIRMS=Example uv run python -m jobscraper.board_health
```

Static validation rejects unknown or missing fields, stray whitespace, bad or duplicate tokens and inconsistent firm metadata. `--live` also fetches the board; an empty one fails unless `--allow-empty` is given. The built package bundles board files, so rebuild after changes.

Removing a board leaves its rows and Supported companies entry behind, because nothing prunes a firm that is no longer fetched. **Do not rename a `firm` casually.** It is part of every row's identity: [Known gap](blog.md#known-gap-renaming-a-firm).

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

23:00 UTC is 07:00 Singapore time the next day. On a TOML push, Daily Scrape fetches only added or modified firms; a removal-only push scrapes nothing. Manual Daily Scrape takes `force_prune_firms`.

The four sheet writers run `pytest` first and share the `sheet-write` concurrency group. The first three then audit the sheet even after a failure and keep `data-quality.json` for 14 days. The audit fails on broken keys or headers; blank metadata and cross-tab duplicates are advisory. It repairs nothing.

## Backlog

- Dedicated integrations: Gojek, Shopify, Zoom, Meta, Google, The Trade Desk, Apple, Atlassian. X runs through SmartRecruiters. Endpoint notes for Google and Apple: [docs/google-apple.md](docs/google-apple.md).
- Citadel SG/HK browser access ([docs/citadel.md](docs/citadel.md)).
- A lock shared by local and Actions writers; numeric seniority such as Engineer II.
- A searchable, mobile-friendly site on the Hetzner server, replacing the sheet: sign-in, approved users, revocable access on pages and data, first-seen dates kept, no duplicate alerts. Needs a database, scheduling, HTTPS and backups.
- Limits: Sheets quotas, full-tab reads, provider rate limits and the cell cap bound scale; no capacity is measured.
