# Repository guidance

## Commands

```bash
uv sync --locked
uv run pytest
uv run jobscraper-validate-boards
uv run python -m jobscraper.board_health
uv run python -m jobscraper.main
uv run python -m jobscraper.sheets.backfill
uv run python -m jobscraper.sheets.full_refresh
uv run python -m jobscraper.sheets.supported_companies
uv run python -m jobscraper.sheets.audit
```

See [README.md](README.md) for setup, configuration and workflow behavior.
Classification rules are in [docs/rules.md](docs/rules.md) and the design
rationale is in [blog.md](blog.md). Edit the rules doc when you change a
keyword list, country set or location rule.

## Production writes

Before running a local command against the production sheet, confirm the
checkout is clean, committed and pushed to `origin/main`. Check `git status`
and `git log origin/main..HEAD` using a current remote reference. Local
schema migrations must use the same code as scheduled production runs.
GitHub Actions dispatches can select another ref; use `main` for production.

The four sheet-writing workflows share `sheet-write` concurrency. Local
runs have no shared lock; do not overlap them with another writer.

After deploying changes to descriptions, their allowlist, schema or
formatting, run Full Refresh to update existing rows. After seniority-only
changes, Backfill is sufficient. For category or region changes, run Full
Refresh before the next daily scrape to preserve first-seen dates and avoid
notifications for moved rows and for open postings the change newly
matches, which Full Refresh adds silently. Repository tests do not require
live writes.

## Code map

- `board_config.py`: environment-independent TOML loading and static checks.
- `config.py`: application settings, validated boards, provider names and
  labels. Sheet credentials are required when opening a sheet, not at import
  time.
- `validate_boards.py`: static configuration and optional live endpoint checks.
- `board_health.py`: fetches every board without opening the sheet.
- `job_boards/fetchers.py`: shared provider registry and per-board invocation.
- `job_boards/common/`: helpers shared by every client.
  - `http.py`: the shared session, retries and response validation.
  - `pagination.py`: complete-snapshot pagination strategies.
  - `jobs.py`: `Job` building: source label, seniority, identity and the
    description allowlist.
- `job_boards/*/client.py`: provider payloads to `Job` objects.
- `models.py`: `Job`, `FIELDS`, date/text handling and URL formulas.
- `enrichment/`: role, seniority, region and office normalization.
- `routing.py`: the managed job tabs and the one rule that puts a job on a
  tab, shared by the scrape, Full Refresh, Backfill and the audit.
- `cli.py`: logging setup and the Actions step summary for entry points.
- `main.py`: fetch, classify, guard, append, prune, summarize and notify.
- `sheets/writer.py`: sheet access, schema checks, filters and prune planning.
- `sheets/backfill.py`: shared row recomputation and in-place refresh.
- `sheets/full_refresh.py`: atomic data replacement and rerouting across tabs.
- `sheets/supported_companies.py`: additive firm reference and announcements.
- `sheets/audit.py`, `quality.py`, `coverage.py`: structural checks and gaps.
- `telegram.py`, `alerts.py`: optional digest, announcements and operator DMs.
- `changed_boards.py`: parsed TOML comparisons for CI selection.

## Invariants

- `FIELDS`, `Job.to_row()` and sheet headers must agree. Daily operations
  reject missing or mismatched headers. Backfill migrates existing empty
  tabs too. Job tabs must already exist; the company-reference tab can be
  created automatically.
- Posting identity is `(firm, job_id)`. Validate external IDs before using
  them. Missing or duplicate IDs invalidate a board snapshot; silently
  skipping them would allow false closures.
  A multi-token firm is eligible for pruning only when every token succeeds.
  Apple reorders tied rows across its pages, so a row repeated on two pages
  is repaired by refetching them, not rejected. The distinct rows must still
  reach the total, and the first page is rechecked for postings added
  mid-fetch.
- Plan the prune guard across all job tabs before any job append. Hold both
  appends and prunes for flagged firms so repeated runs keep the same
  baseline. `FORCE_PRUNE_FIRMS` overrides a hold for confirmed closures.
- Full Refresh builds and validates every row before one Sheets batch commits
  the data and grid sizes. Keep those writes atomic across tabs. Formatting
  follows separately. Do not add per-tab destructive writes to the commit loop.
- Preserve basic-filter criteria and sort settings, changing their range
  only. If metadata cannot be read, fail without replacing the filter.
- External text must remain literal. Full Refresh uses typed cell values;
  other writes escape formula-like text. Only generated URL formulas are
  intentional formulas. Dates use ISO display formatting.
- Preserve provider country information. Normalize separate offices and join
  with ` | `; do not split an ambiguous free-text office list on commas.
  - GovTech, Twitch, Scale AI and Lyft explicitly use the posting's location
    label, not its office list. GovTech's offices name teams, Twitch lists
    the same placeholder office on every posting, and Scale AI and Lyft tag
    placeholders or workspace names that disagree with the real location.
  - Amazon builds offices from its structured `locations` entries, because
    `normalized_location` ends in an ISO alpha-3 code. Each office uses the
    shared alpha-3 table first, then Amazon's country name, whose own forms
    for China and Hong Kong the region rules don't recognize.
  - Apple returns one row per office. Group rows by `positionId`; each
    office uses the alpha-3 code in `countryID`, then Apple's country name.
  - TikTok names one city per posting. Its country is the top of the
    city's parent chain.
- Region decisions live in `classify_region.py`; keep its country sets
  disjoint. Exclude a posting only if every office is excluded. Unknown
  locations remain visible in coverage reports.
- Role category precedence is intentional. Full rules: [docs/rules.md](docs/rules.md).
  - Noise titles: recruiter, solutions-architect, legal-engineer, developer
    relations/advocacy, and leads of customer-facing engineering teams
    ("Sales Engineering Manager").
  - Data-engineering keywords alone do not make a product, program, project,
    technical-writer or service-delivery-manager title an engineering role.
  - QT/QR excludes discretionary, sales, OTC, floor and financing traders.
  - Bare research titles depend on `company_type`.
  - Amazon's own keywords and noise apply only when `source` is Amazon. The
    noise covers every data-centre role, Infrastructure Reliability &
    Quality, QA and SDET, delivery consultants and program managers.
  - Amazon's "Software Development" job-family fallback runs last. It never
    overrides a veto or a title naming a non-software job.
  - Apple's own keywords, noise and team fallbacks apply only when `source`
    is Apple. Its team fallbacks also need the title to name the job, and
    skip test, hardware and data-centre titles. Its internship guard makes
    a non-tech internship type noise unless the title also names software,
    ML/AI or security work.
  - Every caller of `classify_job` must set `company_type`, `source` and
    `department`.
  - Keep provider-specific rules out of the shared lists, so other firms are
    unaffected.
- Greenhouse employment type is a seniority fallback. Refresh open jobs from
  current board data so metadata-only internships retain their level.
- Description extraction remains limited to `FIRMS_NEEDING_DESCRIPTION`.
  Keep salary availability distinct from provider support: current clients
  extract Lever/Ashby salary; Greenhouse exposes optional structured ranges
  through its detail API, which this project does not currently request.
- Supported companies retains existing firms absent from configuration.
  `ONLY_FIRMS` must never reduce that reference tab to the scrape subset.
- Notifications use newly appended rows. Failures are best effort and have
  no durable retry queue. A thread ID is optional; a token and destination
  chat ID enable each Telegram feature independently. Telegram errors must
  never include the request URL, which embeds the bot token.

## Tests and maintenance

Tests overwrite integration environment settings before importing config,
disable dotenv, and block unmocked requests. Use fake responses and sheet
objects; do not use production credentials in tests. Cover failure behavior,
including incomplete pagination and rejection of atomic writes.

Keep provider enumeration in the shared registry and keyword matching in
`enrichment/keywords.py`. Build jobs with `common.jobs.build_job` and route
them with `routing.route_job` rather than repeating either in a client or a
command. Avoid repeating implementation walkthroughs or
live board counts in documentation. Add new provider fixtures with actual
response fields and record API limitations precisely, with official sources
where available. There is no configured formatter or linter.

When writing documentation, don't write one long paragraph, especially when
listing items or features. Use bullets, or split the text over several lines.

The README Backlog lists the remaining enhancements. They are outside the
current implementation.

Do not build or propose an integration for a job board behind a Cloudflare
challenge (HTTP 403 with a `cf-mitigated: challenge` header). Citadel was
removed for this reason. Details and how to recognise one:
[docs/cloudflare-challenges.md](docs/cloudflare-challenges.md).
