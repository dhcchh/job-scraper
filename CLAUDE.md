# CLAUDE.md

## Commands

```bash
uv sync
cp .env.example .env

uv run python -m jobscraper.main              # live scrape: append new and prune closed jobs
uv run python -m jobscraper.sheets.backfill   # refresh existing rows after metadata/classifier changes

uv run pytest
uv run pytest tests/sheets/test_writer.py
```

There is no formatter or linter. Tests never use the network or live Google
credentials: `tests/conftest.py` supplies dummy config and sheet tests use a
small fake `Worksheet`.

## Architecture and invariants

- The daily GitHub Actions workflow runs `jobscraper.main`. Each run fetches
  every configured Greenhouse, Lever, Ashby, and SmartRecruiters board,
  classifies postings into ten tabs (five categories × `PAC`/`RoW`), appends
  jobs first seen this run, then removes jobs confirmed closed.
- `config.py` is the only module that reads environment variables,
  `company_toml/greenhouse_boards.toml`, `company_toml/lever_boards.toml`,
  `company_toml/ashby_boards.toml`, and
  `company_toml/smartrecruiters_boards.toml`; import `jobscraper.config`
  rather than copying its settings at import time. Board tokens are checked
  in and are not secrets.
  `_load_boards` validates every `[[boards]]` entry has `token`, `firm`, and
  `company_type` at import time, raising `ValueError` naming the offending
  entry and field(s) rather than letting a typo surface as a `KeyError` deep
  in `main.py`'s fetch loop.
- `Job` in `models.py` is the shared data shape. Keep `FIELDS`,
  `Job.to_row()`, and the sheet header aligned. The row identity is
  `(board, job_id)`, not `job_id` alone, because Greenhouse IDs are tenant
  scoped. `first_published` is the best available Greenhouse "went live"
  signal; `updated_at` can change after edits and `scraped_date` is only when
  this project first saw the job. `source` records which board system
  ("Greenhouse"/"Lever"/"Ashby"/"SmartRecruiters") a posting came from -- distinct from
  `board`, which is the firm's name -- and is stamped as a fixed literal by
  each `job_boards/*/client.py`, not read from any API response.
  `description` is the full JD text, but ONLY for a firm in
  `models.FIRMS_NEEDING_DESCRIPTION` -- currently just Qube Research &
  Technologies and Squarepoint, the two confirmed cases whose own apply
  page can't be trusted to show the JD at all (Qube's JD only ever lands
  in a client-side hydration blob, invisible/unsearchable on the rendered
  page; Squarepoint's `absolute_url` redirects to a generic careers
  listing rather than the specific posting). Every other posting's
  `description` is "" -- this is a deliberate trial scoped to those two
  firms, not a blanket rollout, so `sheets/backfill.py` actively blanks
  `description` back to "" for any row whose firm isn't in that set
  (unlike every other column, which is never guess-blanked). Where it is
  populated: Greenhouse's client runs `models.strip_html` over its HTML
  `content` field, Lever's and Ashby's clients use each API's own
  ready-made plain text instead. SmartRecruiters' client never populates it
  regardless of `FIRMS_NEEDING_DESCRIPTION` -- see
  `job_boards/smartrecruiters/client.py` for why. `FIELDS`' current order is
  `first_published, updated_at, board, title, location, seniority,
  company_type, url, department, job_id, scraped_date, source,
  description` (columns A-M) -- see `models.py`'s `FIELDS` comment for why.
- `greenhouse/client.py` fetches the public Job Board API with `content=true`.
  It retries transient failures three times; `main.py` logs and skips a board
  that still fails so it cannot trigger false pruning elsewhere.
- `lever/client.py` fetches the public Postings API (`?mode=json`) the same
  way, with the same retry/skip behavior. Lever's response has no edited-at
  timestamp at all, so `first_published` comes from `createdAt` and
  `updated_at` is left `""` rather than fabricated; `department` falls back
  to `categories.team` when a board (e.g. Palantir) never sets
  `categories.department`.
- `ashby/client.py` fetches the public Posting API
  (`api.ashbyhq.com/posting-api/job-board/<slug>`), same retry/skip
  behavior. Same no-edited-at-timestamp shape as Lever: `first_published`
  comes from `publishedAt`, `updated_at` stays `""`; `department` falls back
  to `team` the same way Lever's falls back to `categories.team`, though no
  live Ashby board has needed that fallback yet. Ashby splits its per-office
  list across two fields (top-level `location` plus
  `secondaryLocations[].location`), not one array like Greenhouse/Lever.
- `smartrecruiters/client.py` fetches the public Posting API
  (`api.smartrecruiters.com/v1/companies/<identifier>/postings`), same
  retry/skip behavior. Paginated (the server caps `limit` at 100 regardless
  of what's requested), unlike the other three boards' single-response
  payloads, so the client pages through `offset` until `totalFound` is
  reached. Same no-edited-at-timestamp shape as Lever/Ashby:
  `first_published` comes from `releasedDate`, `updated_at` stays `""`.
  `department` reads `function.label` (a standardized, always-populated
  SmartRecruiters taxonomy), not the generic `department` object every
  posting carries -- confirmed always empty `{}` on both Grab's and Wise's
  live boards, with each company's actual org-unit naming buried in its own
  inconsistent custom fields instead (Grab has a "Department Name" custom
  field; Wise has none, its closest equivalent is "Tribe"/"Squad", its own
  internal jargon). `url` is built directly as
  `https://jobs.smartrecruiters.com/<company identifier>/<id>` (confirmed
  against live postings) rather than read from a field, since the list
  response's own `ref` field is the API endpoint URL, not a candidate-facing
  page. A SmartRecruiters posting is only ever open in one office (a single
  `location` object, not a pre-split per-office array like the other three
  boards), so its client's `_location` normalizes just `location.city` with
  no `" | "`-joining needed.
- Build locations from Greenhouse's `offices[]`, Lever's
  `categories.allLocations`, Ashby's `location` +
  `secondaryLocations[].location`, or SmartRecruiters' single `location.city`,
  normalize each office, remove duplicates, then join with `" | "`. Do not
  split `location.name`/`categories.location` by commas: it is free text.
  `normalize_location.py` intentionally falls back to a cleaned unknown
  value rather than guessing. Add a canonical mapping before treating a new
  city as PAC. It also removes `Remote -`, `(Remote)`, `Office`,
  Stripe-style `Location(s)`, a trailing 2-letter country code in parens,
  Spotify-style `(... Mix)` work-arrangement annotations, a repeated "City,
  City" wrapper, and (seeded from Ashby's Airwallex/Snowflake boards) a
  spaced-out ISO-code prefix like `"AU - Sydney"` before lookup.
- `classify_region` routes a posting to PAC if any normalized office is in a
  PAC country. `_PAC_COUNTRIES` deliberately excludes Malaysia, Indonesia,
  Thailand, the Philippines, and Cambodia even though Grab's SmartRecruiters
  board posts the majority of its jobs there -- a conscious decision (not an
  oversight) when SmartRecruiters/Grab was added, to keep today's
  Singapore/Hong Kong/Australia/Japan/South Korea/China/Taiwan/New
  Zealand/Vietnam scope rather than widen it; those Grab postings land in
  RoW. `classify_roles.classify_job` returns `None` for recruiter titles
  before applying category keywords; keyword precedence is intentional.
- `sheets/writer.py` deduplicates and prunes by `(board, job_id)`. Only prune
  boards fetched successfully (and all tokens for a multi-token firm).
  Pruning uses an atomic Sheets batch of row deletions, but concurrent writers
  can still duplicate appends. Use `USER_ENTERED` values so Sheets preserves
  dates and linkifies URLs.
- Each job tab has a conditional formatting rule
  (`=AND(ISNUMBER($A2), $A2>=TODAY()-7)`) highlighting columns A–K green when
  `first_published` falls within the past 7 days. Like all sheet formatting, it
  survives append and prune runs for free. The cell reference hardcodes
  wherever `first_published` currently sits in `FIELDS` (column A) -- it's
  UI-managed, not something `sheets/backfill.py` rewrites, so reordering
  `FIELDS` again means updating this formula by hand in every tab too.
  Each tab also has a basic filter (`sheets.writer.set_basic_filter`,
  applied by `sheets/backfill.py`) giving every column a dropdown
  filter/sort arrow -- board, location, seniority, and company_type are the
  ones postings are actually meant to be sliced by, but Sheets' basic filter
  is all-or-nothing over its range, so every column gets one. The
  `description` column is set to `wrapStrategy: CLIP`
  (`sheets.writer.format_description_column`, also applied by
  `sheets/backfill.py`) rather than left to Sheets' default WRAP -- a JD's
  embedded newlines (see `models.strip_html`) would otherwise auto-expand
  that row to dozens of lines tall and make scrolling through the tab
  painful; CLIP keeps every row a single line, with the full text still
  readable by clicking into the cell.
- `sheets/backfill.py` is manual only. Run it after changing `FIELDS`,
  classification metadata, location normalization, `first_published`, or
  `models.FIRMS_NEEDING_DESCRIPTION`. It refreshes cells in place
  (including recomputing `company_type` and `source` by looking a row's
  `board` up in the current `*_boards.toml` files, refreshing
  `location`/`first_published` from a fresh re-fetch of every board for
  still-open postings, and recomputing `description` from
  `FIRMS_NEEDING_DESCRIPTION` -- filling it in from that same fresh
  re-fetch and overwriting even a non-empty value for a firm in the set
  since a JD can be legitimately edited, blanking it back to "" for every
  other firm) but deliberately does not move rows between tabs or rename
  board values, which could lose data if interrupted.
- The scraper logs the spreadsheet title/URL and writes a GitHub Actions job
  summary. Workflow-level concurrency protects Action-triggered runs; it does
  not protect a local run pointed at the production sheet.
- `GOOGLE_SHEET_ID` is set independently in `.env` (local) and the GitHub
  Actions secret (CI); nothing else cross-checks the two. The optional
  `GOOGLE_SHEET_TITLE` env var closes that gap: when set, `get_spreadsheet()`
  raises before any board is touched if the opened spreadsheet's title
  doesn't match, rather than only being visible in the logged title/URL.
- `telegram.py` is an optional digest of this run's newly appended DA/BI,
  DS/ML/Algos, and SWE/DE postings -- PAC region only, matching this
  project's Singapore-based audience (see `classify_region.py`); QT/QR and
  SRE/DevOps are deliberately left out. `main.py` calls `send_digest` with
  each category's newly appended PAC-tab jobs plus this run's already-opened
  `spreadsheet.url` -- `sheets.writer.append_jobs` returns the `list[Job]`
  it actually appended (not a count) so the digest can group them. The
  message is deliberately a *count* digest, not a listing: one line per
  distinct (category, firm, location), e.g. "3 new SWE/DE roles from Grab
  available in Singapore", not one line per job with its own title and
  apply link -- an earlier version gave every job's title and a direct
  apply link away up front, which gave a reader no reason to open the
  tracker sheet. The message ends with one link to the spreadsheet itself
  (not a per-job/per-category deep link) so the reader goes there for
  titles, exact locations, and the real apply link. Disabled by default:
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` unset (see `config.py`)
  makes `send_digest` a silent no-op, same convention as
  data-career-hustler-bot's own optional `*_CHAT_ID` destinations. Sent via
  a raw HTTP POST to Telegram's Bot API, not a bot framework -- this fires
  one message (HTML parse mode, since the trailing sheet link needs a real
  anchor tag) and exits, no dispatcher needed. `send_digest`
  never raises: a Telegram outage or bad token is logged and swallowed
  rather than failing a run that otherwise wrote to the sheet successfully,
  and `main.py` calls it unconditionally before its own failed-board check
  so a flaky board elsewhere doesn't withhold the digest for postings a
  *different*, successful board actually appended. `TELEGRAM_BOT_TOKEN` can
  reuse the exact bot token the sibling `data-career-hustler-bot` repo's
  Telegram community bot already runs (a bot token isn't tied to one chat),
  but `TELEGRAM_CHAT_ID`/`TELEGRAM_THREAD_ID` should point at a different
  topic from that bot's own `JOBS_CHAT_ID`/`JOBS_THREAD_ID` -- these are
  unreviewed scraped postings, not the same thing as that bot's
  member-curated, point-earning `/postjob` listings, and mixing the two
  would make the manual topic unreadable. A single message over Telegram's
  4096-character cap splits into a few, on section/line boundaries only
  (`telegram._chunk`) -- never mid-line, since a truncated `<a href=...>`
  would send Telegram malformed HTML.

## Open work

- Recognize numeric and level-based seniority (for example, `Engineer II`,
  `SDE II`, and `L4/L5`) rather than classifying it as `Unspecified`.
- If more than one person operates the production sheet, add a distributed
  lock; GitHub Actions concurrency cannot prevent an overlapping local run.

## Scale

The current sequential fetcher and Google Sheet datastore fit a small-to-
medium board list. As history and board count grow, full-sheet reads, API
retries/rate limits, and Sheets' cell cap become the limiting factors. For a
much larger deployment, move to concurrent/batched fetching, a database, and
a dedicated scheduler.
