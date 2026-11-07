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
- `job_boards/fetchers.py`: shared provider registry and per-board invocation.
- `job_boards/_http.py`: retries, response validation and pagination.
- `job_boards/*/client.py`: provider payloads to `Job` objects.
- `models.py`: `Job`, `FIELDS`, date/text handling and URL formulas.
- `enrichment/`: role, seniority, region and office normalization.
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
  skipping them would allow false closures. The one exception is Workday
  list items with neither a title nor a link: they count toward the total
  but aren't postings, so they're skipped, up to a small per-board allowance.
  A multi-token firm is eligible for pruning only when every token succeeds.
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
  GovTech explicitly uses its location label because its office list names teams;
  Twitch does too because every posting lists the same placeholder office.
- Region decisions live in `classify_region.py`; keep its country sets
  disjoint. Exclude a posting only if every office is excluded. Unknown
  locations remain visible in coverage reports.
- Role category precedence is intentional. Recruiter, solutions-architect
  and legal-engineer titles are noise, as are developer relations/advocacy
  titles and leads of customer-facing engineering teams ("Sales Engineering
  Manager"). Data-engineering keywords alone do not make a
  product/program/project manager an engineering role. QT/QR excludes
  discretionary, sales, OTC, floor and financing traders. Bare
  research titles depend on `company_type`, so every caller of
  `classify_job` must set it.
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
`enrichment/_keywords.py`. Avoid repeating implementation walkthroughs or
live board counts in documentation. Add new provider fixtures with actual
response fields and record API limitations precisely, with official sources
where available. There is no configured formatter or linter.

Remaining enhancements: numeric/level-based seniority (such as Engineer II),
a lock shared by local and Actions writers, and integrations in the README
backlog. These are outside the current implementation.
