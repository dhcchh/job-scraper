# CLAUDE.md

## Commands

```bash
uv sync
cp .env.example .env

uv run python -m jobscraper.main              # live scrape: append new and prune closed jobs
uv run python -m jobscraper.sheets.backfill   # refresh existing rows after metadata/classifier changes
uv run python -m jobscraper.sheets.full_refresh  # also re-routes rows across tabs after a category/region change

uv run pytest
uv run pytest tests/sheets/test_writer.py
```

**Before running `jobscraper.main`, `sheets.backfill`, `sheets.full_refresh`,
or `sheets.supported_companies` against the real production sheet** (a real
`.env`, not the test-forced dummy values) **from a local checkout: confirm
that checkout is committed AND pushed to `origin/main` first** (`git status`
clean, `git log origin/main..HEAD` empty). GitHub Actions always runs
`origin/main`, never whatever's on disk locally -- if a local run migrates
live sheet state (schema, column order, row shape) using code that hasn't
been pushed yet, the scheduled workflow keeps running the old code against
the now-already-migrated sheet, and the two silently diverge. This isn't
hypothetical: it happened on 2026-09-21 -- `models.FIELDS`'s column order was
changed locally and applied to the production sheet's header via a local
`sheets.backfill` run before that same reorder was committed and pushed. The
daily scheduled workflow, still running the old (unpushed) column order,
kept appending rows shifted relative to the sheet's new header, which
corrupted `job_id` in every newly appended row; `prune_closed_postings` then
read that corrupted `job_id`, concluded the posting had closed, and silently
deleted nearly every row appended that same run -- every day, for two days,
with the workflow still reporting success each time. Running these commands
only from a clean, pushed checkout is what keeps local and CI-scheduled
behavior identical.

There is no formatter or linter. Tests never use the network or live Google
or Telegram credentials: `tests/conftest.py` forces `GOOGLE_SHEET_ID`/
`GOOGLE_SERVICE_ACCOUNT_JSON` to dummy values and `TELEGRAM_BOT_TOKEN`/
`TELEGRAM_CHAT_ID`/`TELEGRAM_THREAD_ID` to `""`, all via `setdefault` *before*
`jobscraper.config` is ever imported -- config.py's own `load_dotenv()` call
never overrides a key already in `os.environ` (its default `override=False`),
so this reliably wins over whatever a real, locally configured `.env` has,
without editing or reading that file. Skipping the Telegram half of this
once let a real `.env`'s real bot token/chat id flow straight through into
every `tests/test_main.py` test that calls `main.run()` without itself
monkeypatching `send_digest` -- each one actually posted its fake job data
(e.g. "Acme", "GoodCo") to the real chat on every `pytest` run. Sheet tests
use a small fake `Worksheet`.

## Architecture and invariants

- Two GitHub Actions workflows write to the production sheet, sharing a
  `sheet-write` concurrency group so they queue rather than overlap (see
  `.github/workflows/`). "Daily Scrape (Append)"
  (`daily-scrape.yml`, cron + manual `workflow_dispatch`) runs
  `jobscraper.main`: fetches every configured Greenhouse, Lever, Ashby,
  SmartRecruiters, Eightfold, and Workday board, classifies postings into ten tabs
  (five categories × `PAC`/`RoW`), appends jobs first seen this run, then
  removes jobs confirmed closed. "Full Refresh (Rebuild)"
  (`full-refresh.yml`, manual `workflow_dispatch` only, no schedule) runs
  `jobscraper.sheets.full_refresh` -- see its own bullet below.
- `config.py` is the only module that reads environment variables,
  `company_toml/greenhouse_boards.toml`, `company_toml/lever_boards.toml`,
  `company_toml/ashby_boards.toml`,
  `company_toml/smartrecruiters_boards.toml`,
  `company_toml/eightfold_boards.toml`, and
  `company_toml/workday_boards.toml`; import `jobscraper.config`
  rather than copying its settings at import time. Board tokens are checked
  in and are not secrets.
  `_load_boards` validates every `[[boards]]` entry has `token`, `firm`, and
  `company_type` at import time, raising `ValueError` naming the offending
  entry and field(s) rather than letting a typo surface as a `KeyError` deep
  in `main.py`'s fetch loop. All six board types need only that same
  `token`/`firm`/`company_type` shape -- Eightfold's `token` is its own
  subdomain (`<token>.eightfold.ai`), same idea as Lever's/Ashby's slug; see
  `job_boards/eightfold/client.py`. Workday's packs the three per-company
  pieces of its careers-site URL into one `"<tenant>.<wdN>/<site>"` string
  (e.g. `"visa.wd5/Visa"`); see `job_boards/workday/client.py`.
- `Job` in `models.py` is the shared data shape. Keep `FIELDS`,
  `Job.to_row()`, and the sheet header aligned. The row identity is
  `(board, job_id)`, not `job_id` alone, because Greenhouse IDs are tenant
  scoped. `first_published` is the best available Greenhouse "went live"
  signal; `updated_at` can change after edits and `scraped_date` is only when
  this project first saw the job. `source` records which board system
  ("Greenhouse"/"Lever"/"Ashby"/"SmartRecruiters"/"Eightfold"/"Workday") a posting came from -- distinct from
  `board`, which is the firm's name -- and is stamped as a fixed literal by
  each `job_boards/*/client.py`, not read from any API response.
  `description` is the full JD text, but ONLY for a firm in
  `models.FIRMS_NEEDING_DESCRIPTION` -- currently just QRT (Qube Research &
  Technologies) and Squarepoint, the two confirmed cases whose own apply
  page can't be trusted to show the JD at all (QRT's JD only ever lands
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
  regardless of `FIRMS_NEEDING_DESCRIPTION` at all -- see
  `job_boards/smartrecruiters/client.py` for why (the full JD isn't in its
  list response at all). Eightfold's client gates `description` on
  `FIRMS_NEEDING_DESCRIPTION` the same way Greenhouse/Lever/Ashby do, but
  it's moot today: no live Eightfold firm is in that set, and even if one
  were, the list response's own `job_description` field is always "" on
  every live posting checked, so the gate never actually has anything to
  populate -- see `job_boards/eightfold/client.py` (it's also unconfirmed
  whether Eightfold even has a separate per-posting detail endpoint with
  real JD text, which is what a future Eightfold firm needing this would
  have to add). Workday's client never populates it either: the JD is only
  on each posting's detail endpoint (`jobDescription`), which it doesn't
  call for that since no Workday firm is in `FIRMS_NEEDING_DESCRIPTION`.
  `FIELDS`' current order is
  `first_published, updated_at, board, title, location, seniority,
  company_type, url, salary, department, job_id, scraped_date, source,
  description` (columns A-N) -- see `models.py`'s `FIELDS` comment
  for why. **After every code or configuration change that affects
  `description`** (its extraction, allowlist, formatting, or column
  placement), the production spreadsheet must be edited by running
  `uv run python -m jobscraper.sheets.full_refresh` or the manual
  "Full Refresh (Rebuild)" workflow. Daily scraping only appends unseen
  postings, so it cannot update the existing description cells.
- `Job.salary` is a human-readable pay range, or `None` -- unlike `description`,
  NOT gated to a firm allowlist; it's populated whenever a posting itself
  discloses one, `None` otherwise, since disclosure varies posting to posting,
  not firm to firm. Only two of the six board types expose anything
  structured to read this from. Lever's `salaryRange`
  (`{min, max, currency, interval}`) is present only when that employer
  opted in to Lever's own pay-transparency feature -- confirmed present on
  Belvedere Trading's live board, absent on this project's other four Lever
  boards; `job_boards/lever/client.py`'s `_salary` formats it as e.g. "USD
  55,000-65,000/year". Ashby's `compensation` object is omitted from the
  response entirely unless the request adds `includeCompensation=true` --
  confirmed silently absent otherwise on every board tried -- so
  `job_boards/ashby/client.py`'s `fetch_ashby_jobs` always requests it, and
  `_salary` reads `compensation.scrapeableCompensationSalarySummary` (a
  short string Ashby already formats for exactly this purpose, e.g. "$257K
  - $335K"), not `compensationTierSummary` (the fuller string Ashby's own
  hosted page shows, which also appends "Offers Equity"/"Offers
  Commission" -- too much for one sheet cell). Confirmed present on 13 of
  this project's 22 Ashby boards (e.g. OpenAI 673/818 jobs, Exa 54/54,
  Sierra 183/209), absent on the rest (e.g. Notion, Cursor, Mistral).
  Greenhouse has no structured field at all -- confirmed across
  Stripe/Airbnb/Affirm/GitLab/Robinhood's live boards -- a pay range, when
  disclosed, is only ever freeform text buried in `content` (e.g.
  Robinhood: "the salary range for this role is ... aligned to one of 3
  compensation zones"), in no consistent shape worth regexing out, so
  `job_boards/greenhouse/client.py` always sets `salary=None`.
  SmartRecruiters, Eightfold, and Workday expose no compensation field
  anywhere in their public APIs either (confirmed against Grab's
  per-posting detail endpoint, Millennium's live board, and Visa's/
  Mastercard's list and detail endpoints, respectively), so all three
  clients always set `salary=None` too. `sheets/backfill.py` treats `salary` the same
  as `location` -- a fresh re-fetch (when the posting is still open)
  unconditionally overwrites whatever's in the cell, including an empty cell if
  disclosure was removed, rather than only filling in a blank the way
  `first_published` does; a closed posting's row, with no override
  available, is left exactly as it already is.
- All six board clients share one retry policy,
  `job_boards/_http.get_with_retries` (and `post_with_retries`, for
  Workday's POST search endpoint) (3 attempts, exponential 2s/4s
  backoff, retrying only a connection error/timeout or a 5xx/429
  HTTPError -- anything else, e.g. a bad/renamed board token, raises
  immediately) -- tested once in `tests/job_boards/test_http.py` rather
  than once per board. `main.py` logs and skips a board whose fetch still
  fails after those retries, so it cannot trigger false pruning elsewhere.
- `greenhouse/client.py` fetches the public Job Board API with `content=true`.
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
  reached, via the shared `job_boards/_http.paginate_with_retries` --
  Eightfold's client pages the same way, so this loop (advance the cursor
  by however many items the page actually returned, stop at the response's
  own total or an empty page) lives once rather than once per paginated
  board. Same no-edited-at-timestamp shape as Lever/Ashby:
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
  `location` object, not a pre-split per-office array like the other four
  boards), so its client's `_location` normalizes just `location.city` with
  no `" | "`-joining needed.
- `eightfold/client.py` fetches a company's own Eightfold Apply API
  (`<slug>.eightfold.ai/api/apply/v2/jobs`), same retry/skip behavior --
  confirmed against Millennium's live board (`mlp`). This is a real but
  UNDOCUMENTED endpoint -- found by inspecting a real customer's rendered
  careers page, not a published API contract, so unlike Greenhouse/Lever/
  Ashby/SmartRecruiters there is no guarantee Eightfold (or Workday, the
  same story) won't change its shape or start requiring auth without
  notice. Unlike those four boards, there is no single shared host: each company gets its own Eightfold subdomain, so
  `token` (company_toml/eightfold_boards.toml) is that subdomain, not a
  path segment off a common base URL -- a `domain` query param (the
  company's own corporate domain) shows up in the site's own browser
  traffic, but confirmed identical responses with and without it (and a
  wrong domain 404s rather than 403s), so it's left off rather than
  carrying a second, redundant identifier per board in company_toml.
  Paginated via `start`/`num` and the same shared
  `job_boards/_http.paginate_with_retries` SmartRecruiters' client uses,
  but the server always returns exactly 10 positions per page regardless
  of what `num` asks for (confirmed requesting up to 500), so the client
  pages in fixed strides of 10 using the response's own `count` as the
  stopping point, not a client-chosen/server-capped page size like
  SmartRecruiters', meaning a firm with Millennium's ~230 open postings
  costs ~23 requests where a similarly-sized Greenhouse/Ashby board costs
  1-3. Each returned posting's `id` is also deduped within the run (not
  just against the sheet's existing rows) in case a short final page ever
  repeats an id instead of coming back shorter than the page size --
  unconfirmed either way, since only varying `num` at a fixed `start` has
  been tested. Most Eightfold tenants
  block this query pattern outright -- confirmed, not just suspected: of 7
  real, verified Eightfold-hosted firms tried (found via each one's own
  actual careers page, not a guessed subdomain), only Millennium and
  Albemarle were reachable this way; Morgan Stanley, Micron, Eaton, and
  Vodafone all returned the same 403 `{"message": "Not authorized for
  ..."}` even from their own live subdomain. Each Eightfold tenant clearly
  configures its own WAF/bot-protection independently, so a new Eightfold
  firm needs its own dry-run before being added, same as every other board
  type's "Adding a board" workflow -- expect roughly 2 in 7 to actually
  work, not an assumption it'll work because Millennium's does.
  Unlike Lever/Ashby/SmartRecruiters, Eightfold gives a genuine `t_update`
  separate from `t_create` (both Unix epoch seconds, confirmed differing on
  real Millennium postings), so both `first_published` and `updated_at` are
  populated, same as Greenhouse. `department` is a plain, always-populated
  top-level string. `locations[]` is Eightfold's own pre-split per-office
  list (same role as the other boards' own pre-split location fields) but
  each entry is itself a hierarchical `"City, Region, Country"` string
  rather than a bare city -- `_location` keeps just the leading city
  segment before normalizing, since normalize_location.py's table is keyed
  by bare cities and already knows the right country; this is also what
  collapses Eightfold's own redundant `"Tokyo, Tokyo, Japan"`/`"Shanghai,
  Shanghai, China"` duplication into the clean form every other board
  already produces, with no changes needed to normalize_location.py
  itself. `job_id` is Eightfold's own internal numeric `id` (what
  `canonicalPositionUrl` is keyed on), not `ats_job_id` (the underlying
  company ATS's own requisition number, e.g. "REQ-30793"). The list
  endpoint's own `job_description` is always `""` on every live posting
  checked, so `description` stays unconditionally `""` here too (no live
  Eightfold board is in `FIRMS_NEEDING_DESCRIPTION`).
- `workday/client.py` fetches a company's own Workday careers-site API
  (`https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>`),
  same retry/skip behavior -- confirmed against Visa's (`visa.wd5/Visa`)
  and Mastercard's (`mastercard.wd1/CorporateCareers`) live boards.
  UNDOCUMENTED like Eightfold's (it's what the careers site's own front-end
  calls), and the `wdN` data-center number varies per company and isn't
  guessable (`visa.wd1` 422s), so take a new firm's token from its real
  careers-site URL. The job list is a POST (a JSON search body), paginated
  via `offset`/`limit` through the shared
  `job_boards/_http.paginate_with_retries` (`method="POST"`); `limit`
  above 20 is a 400, and `total` is only real on the FIRST page -- every
  later page says 0 -- which `paginate_with_retries` handles by keeping the
  first page's total. Each request takes ~1.3s server-side (connection
  reuse barely helps), so one page at a time Mastercard's ~1,050 postings
  (53 pages) took ~2 minutes. The client is therefore the one place this
  project fetches concurrently: `paginate_with_retries(workers=8)` reads
  the first page for the total, then fetches the rest 8 at a time (Visa
  ~14s, Mastercard ~18s, measured; output identical to the sequential
  fetch). If the parallel pass raises or doesn't add up to that total, it
  logs a warning and refetches the whole board one page at a time -- the
  original behavior -- so rate-limiting costs time, not postings.
  `workday/client.py`'s `_WORKERS = 1` forces one-at-a-time everywhere if
  Workday ever starts rejecting the parallel pattern; the pre-parallel
  version is commit `8b47b3e`. A list item carries only `title`, `externalPath`,
  `locationsText`, `postedOn`, and `bulletFields` -- no department, no
  timestamp, no compensation -- so `department`/`updated_at` stay `""`.
  `postedOn` is relative text ("Posted Today"/"Yesterday"/"N Days Ago"/
  "30+ Days Ago"); `first_published` is counted back from `scraped_date`
  (possibly a day off across timezones) and left `""` for "30+ Days Ago"
  rather than fabricated. A multi-office posting's `locationsText` is just
  "N Locations"; its real offices (`location` + `additionalLocations`,
  falling back to `jobRequisitionLocation` when both are empty) and an
  exact `startDate` only come from its per-posting detail GET, at ~1-4s
  each -- so the client only makes that call when
  `classify_roles.classify_job` routes the posting to a category (~11 of
  ~160 multi-office postings across both boards), since `main.py` drops a
  noise posting before ever reading its location. Detail calls also go 8
  at a time; one that still fails after its retries is tried once more on
  its own, and only then (e.g. a 404 for a posting that closed between the
  two calls) is just that posting dropped for the run rather than failing
  the board. `job_id` is `externalPath`'s suffix after its last `_` -- the
  requisition id plus any "-N" repost counter (e.g. "R-287924-1",
  "REF088713W") -- not `bulletFields[0]`, which is tenant-configured and
  missing on at least one live Visa posting.
- Build locations from Greenhouse's `offices[]`, Lever's
  `categories.allLocations`, Ashby's `location` +
  `secondaryLocations[].location`, SmartRecruiters' single `location.city`,
  Eightfold's `locations[]` (leading city segment only -- see above), or
  Workday's `locationsText` (one office, normalized whole) / detail
  `location` + `additionalLocations` (multi-office -- see above),
  normalize each office, remove duplicates, then join with `" | "`. Do not
  split `location.name`/`categories.location` by commas: it is free text.
  `normalize_location.py` intentionally falls back to a cleaned unknown
  value rather than guessing. Add a canonical mapping before treating a new
  city as PAC. It also removes `Remote -`, `(Remote)`, `Office`,
  Stripe-style `Location(s)`, a trailing 2-letter country code in parens,
  Spotify-style `(... Mix)` work-arrangement annotations, a repeated "City,
  City" wrapper, (seeded from Ashby's Airwallex/Snowflake boards) a
  spaced-out ISO-code prefix like `"AU - Sydney"`, and (seeded from
  Workday's Visa/Mastercard boards) that same prefix dropped entirely when a
  full location or `Remote - <country>` follows it (`"US - Austin, TX"`,
  `"IN - Remote - IND"`) plus a building/acquired-company paren after a
  "City, Country" (`"Jakarta, Indonesia (6th Floor)"`) before lookup --
  both of which otherwise made `is_excluded` miss India/Indonesia/Armenia
  postings. The lookup
  table itself also carries whole-string entries (seeded from Millennium's
  live Eightfold board) for a "City, Region, Country" triple neither the
  bare-city nor "City, Country" conventions above cover (e.g. "Tokyo,
  Tokyo, Japan", "New York, New York, United States of America") --
  `classify_region` still classifies these correctly even unmapped (it only
  reads whatever's after the last comma), so an unmapped one is a
  display-only inconsistency, not a PAC/RoW bug.
- `classify_region` routes a posting to PAC if any normalized office is in a
  PAC country: Singapore/Hong Kong/Australia/Japan/South Korea/China/
  Taiwan/New Zealand/Thailand. `_PAC_COUNTRIES` deliberately excludes every
  other ASEAN country -- Malaysia, Indonesia, Vietnam, the Philippines, and
  Cambodia -- even though Grab's SmartRecruiters board posts the majority
  of its jobs there. Thailand is the one ASEAN exception, added because
  Bangkok pay is comparable -- whole-country, not Bangkok-only, consistent
  with every other entry here being a country rather than a city. This was
  a conscious decision (not an oversight) when SmartRecruiters/Grab was
  added, to keep today's scope rather than widen it.
  `classify_roles.classify_job` returns `None` for recruiter titles before
  applying category keywords; keyword precedence is intentional.
- `classify_seniority.classify_seniority` matches the title (+ department)
  first and falls back to a board's own structured employment-type value
  only when those carry no level wording at all. Today that value comes
  from Greenhouse's per-posting `metadata` field literally named
  "Employment Type" (`job_boards/greenhouse/client.py`'s
  `_employment_type`, matched by name case/whitespace-insensitively since
  `metadata` is a board-defined custom field list, not a fixed schema).
  It exists because some employers never put "Intern" in an internship's
  title: all 46 of Jane Street's summer internships are posted as plain
  "Data Engineer"/"Software Engineer"/... (e.g. job 8631973002, a NYC data
  engineering internship) with "Summer Internship" only in that metadata
  field, so title-only matching read every one of them as "Unspecified".
  Fallback-only is deliberate -- that field can only ever say
  Intern/Graduate in practice, so it has nothing to add to a title that
  already resolved, and the title stays the primary signal. Confirmed
  against all 54 Greenhouse boards' live postings: it changes 71 rows, all
  of them previously "Unspecified" (69 Jane Street, plus one each at Akuna
  and QRT), and moves no already-classified posting to a different level.
  Deliberately this one metadata name only: the other plausible-looking
  ones are either redundant (Jane Street's "Workday Job Profile") or
  actively wrong to feed a seniority classifier (Elastic's "Seniority" is
  "Mid-Senior Level" on 300 postings, which would relabel 142 real
  Principal/Manager/Lead/Director titles as plain "Senior"; MongoDB's "Job
  Level" says "People Manager" on reqs titled Director). Greenhouse-only
  for the same evidence-based reason: dry-running the same comparison over
  Lever's `categories.commitment`, Ashby's `employmentType`, and
  SmartRecruiters' `typeOfEmployment.label` changed nothing at all on any
  configured board of those three types -- every intern posting there
  already says so in its own title -- and Eightfold has no such field
  (its `type` is the string "ATS" on every posting). **After a change
  affecting seniority, existing rows need
  `uv run python -m jobscraper.sheets.backfill`**; daily scraping only
  appends unseen postings, so it cannot relabel rows already in a tab.
- `classify_region.is_excluded` is a separate, stricter gate than PAC/RoW:
  Vietnam, Malaysia, Indonesia, India, Armenia, and Uruguay are excluded
  entirely -- not posted at all, PAC or RoW -- because pay isn't
  comparable to Singapore's and there's no incentive to relocate there. A
  module-level assertion keeps `_PAC_COUNTRIES` and `_EXCLUDED_COUNTRIES`
  disjoint so a country can't silently end up in both. Unlike
  `classify_region`'s "any office is PAC" precedence, `is_excluded`
  requires EVERY office in a (possibly `" | "`-joined) location to be
  excluded before dropping the whole posting -- a job listing Hanoi
  alongside Singapore or even just London still has a real, keepable
  office, so it's kept and classified normally. `main.py`'s per-job loop
  checks this right after `classify_job` finds a category (a noise job is
  already dropped by then, so its location is never checked) and before
  `classify_region` ever runs, incrementing its own `excluded_count`
  separate from `noise_count` in both the log line and
  `_build_summary_markdown`.
  `sheets/full_refresh.py` applies the same check when reclassifying an
  existing row and, unlike its conservative "leave it in place" handling
  of a row whose category becomes `None`, actually removes a
  now-wholly-excluded row from the sheet -- this project has decided
  outright not to carry those postings, so a stale row predating that
  decision shouldn't survive a refresh either.
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
  `location`/`first_published`/`seniority` from a fresh re-fetch of every
  board for still-open postings -- `seniority` comes from that re-fetch
  rather than the row's own title/department because a Greenhouse
  posting's level can live in "Employment Type" metadata the sheet has no
  column for, so re-deriving from the row alone would reset those rows to
  "Unspecified" every run; a closed posting, with no re-fetch to ask,
  falls back to title/department -- and recomputing `description` from
  `FIRMS_NEEDING_DESCRIPTION` -- filling it in from that same fresh
  re-fetch and overwriting even a non-empty value for a firm in the set
  since a JD can be legitimately edited, blanking it back to "" for every
  other firm) but deliberately does not move rows between tabs or rename
  board values, which could lose data if interrupted.
- `sheets/full_refresh.py` is manual only -- by hand, or via the
  "Full Refresh (Rebuild)" GitHub Actions workflow (`workflow_dispatch`
  only, no schedule; see the GitHub Actions bullet above) -- for the one
  thing `backfill.py` deliberately punts on: it also re-routes a row to a
  different tab when
  `classify_roles.classify_job`'s category or `classify_region`'s PAC
  country list puts it somewhere else now (e.g. the Thailand/Vietnam PAC
  swap above). Every other column is recomputed the same way `backfill.py`
  does -- both share `backfill._recompute_row_values` rather than
  duplicating that logic. It reads and reclassifies all ten tabs into
  memory FIRST and only starts writing once every row's destination tab is
  known, so an interrupted run can't strand a row between its old and new
  tab; it is still not one atomic transaction across the ten separate
  writes that follow (no distributed lock -- see Open Work below). A row
  whose title no longer matches any category at all stays on the tab it's
  already on rather than being dropped. A `(board, job_id)` seen on more
  than one tab (shouldn't happen in normal operation) keeps its first
  occurrence and logs a warning for the rest, rather than writing the
  same posting into two tabs.
- `sheets/supported_companies.py` keeps a separate, user-created "Supported
  companies" tab in sync with the checked-in `*_boards.toml` files. Laid out
  wide, not as one row per firm: one column per `company_type`, headed by
  that category's slug (`_slugify`, e.g. "Prop Trading/Hedge Fund" ->
  `prop_trading_hedge_fund`) rather than the raw label, with that category's
  firms listed down the column -- a self-explanatory header per category
  rather than a repeated `Category` value down a long, narrow table.
  Categories have different firm counts, so it's a ragged grid; shorter
  columns are padded with `""` up to the tallest so gspread's `update()`
  gets a rectangular 2D array. `sheets.writer.set_basic_filter` is
  deliberately NOT applied here, unlike every job tab -- it sorts the whole
  range as one set of rows, which would scramble the alignment between
  unrelated categories' columns since a row here isn't a record the way a
  job posting's row is. Unlike `backfill.py`/`full_refresh.py`, this is NOT
  manual-only: `main.py`'s `run()` calls it unconditionally on every daily
  scrape (`sync_supported_companies`), so a firm added to any of the six
  TOML files shows up there on the very next scheduled run with no separate
  step. Safe to run unattended because it reasons about nothing but current
  config, not job/sheet state -- the whole tab is cleared and rewritten from
  scratch each time (so a firm or category removed from every TOML file
  disappears too), deduplicating a multi-token firm (e.g. Optiver) down to
  one entry. The tab is matched by name case/whitespace-insensitively and
  created if genuinely missing, since it's user-created rather than one of
  the ten (category, region) job tabs `main.py` otherwise assumes already
  exist. Also runnable by hand
  (`uv run python -m jobscraper.sheets.supported_companies`) to populate or
  refresh it immediately without waiting for the next scrape.
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
  message is deliberately a *count* digest, not a listing: a bold category
  header followed by one terse "Firm xN (Location)" line per distinct
  (firm, location) group under it, e.g. "**SWE/DE**" then "Grab x3
  (Singapore)" -- not one line per job with its own title and apply link,
  and not the earlier one-full-sentence-per-group format ("3 new SWE/DE
  roles from Grab available in Singapore" for every group), which repeated
  the category name and "new .../roles from/available in" boilerplate on
  every line and turned into a hard-to-scan wall of text on a day with many
  firms. An even earlier version gave every job's title and a direct apply
  link away up front, which gave a reader no reason to open the tracker
  sheet. The message ends with one link to the spreadsheet itself (not a
  per-job/per-category deep link) so the reader goes there for titles,
  exact locations, and the real apply link. Disabled by default:
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
  would send Telegram malformed HTML. `uv run python -m jobscraper.telegram
  --check` sends a one-off test message via `telegram.check_connection()`
  to verify `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` work, without waiting
  for a real run to have new PAC postings -- deliberately the opposite of
  `send_digest`'s "not configured"/failure handling: both are printed and
  returned as `False` rather than no-op'd or logged-and-swallowed, since
  the point of a manual check is to surface what's wrong, not hide it.

## Open work

- Recognize numeric and level-based seniority (for example, `Engineer II`,
  `SDE II`, and `L4/L5`) rather than classifying it as `Unspecified`.
- If more than one person operates the production sheet, add a distributed
  lock; the `sheet-write` GitHub Actions concurrency group only queues the
  two Actions-triggered workflows against each other -- it cannot prevent
  an overlapping local run (`jobscraper.main`, `sheets/backfill.py`, or
  `sheets/full_refresh.py` run by hand) from racing either of them.

## Scale

The current sequential fetcher and Google Sheet datastore fit a small-to-
medium board list. As history and board count grow, full-sheet reads, API
retries/rate limits, and Sheets' cell cap become the limiting factors. For a
much larger deployment, move to concurrent/batched fetching, a database, and
a dedicated scheduler.
