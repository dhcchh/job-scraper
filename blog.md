# How the job scraper works

A daily script that pulls postings from ~70 companies' public job board APIs
(Greenhouse, Lever, Ashby, SmartRecruiters), sorts them into a Google Sheet
by role and region, and pings Telegram when something new shows up in
Singapore/APAC.

## 1. Identifying unique jobs

Every posting is keyed by **`(board, job_id)`** — the firm's name plus that
board system's own id for the posting — not `job_id` alone, since
Greenhouse ids are scoped per tenant and could collide across companies.

This key does two things on every run:

- **Append**: a job whose key is already in the sheet is skipped, so a
  still-open posting doesn't get a fresh row every day it's re-fetched. One
  side effect: since a row is written exactly once, its `scraped_date`
  really means "first seen," not "last confirmed open."
- **Prune**: a job whose key *was* in the sheet but is missing from a
  board's current fetch is deleted — that board actually stopped listing
  it. A board that failed to fetch this run is excluded from pruning
  entirely, so a network hiccup can never look like a wave of closures.

## 2. Pipeline steps

```
for each board (Greenhouse → Lever → Ashby → SmartRecruiters):
    fetch postings (retry transient errors, skip board on repeated failure)
        ↓
classify_job: role category (or drop as noise — HR/Legal/Sales etc.)
        ↓
classify_region: PAC or RoW, from normalized office location
        ↓
route to one of 10 tabs (5 categories × 2 regions)
        ↓
append new (board, job_id) rows, prune closed ones, per tab
        ↓
write a run summary + send a Telegram digest of new PAC postings
```

Nothing here is concurrent — one board at a time, since it runs once a day
and there's no rate-limit pressure at this scale.

## 3. Error handling

- **Per-board isolation**: each board's fetch is wrapped in its own
  try/except. A client already retries transient errors (connection drops,
  timeouts, 5xx, 429) with exponential backoff internally; a 4xx (bad
  token) fails fast since retrying can't fix it. Either way, once a board
  exhausts retries, `main.py` logs it, adds the firm to `failed_firms`, and
  moves on — the run doesn't abort.
- **Failed boards can't cause false pruning**: a failed firm is left out of
  `fetched_boards`, so its still-open postings are never mistaken for
  closed ones just because they didn't show up in this run's fetch.
- **Fail loud, eventually**: successful boards are still fully processed
  and written, but if *any* board failed, the run raises at the very end —
  after the summary is written and the Telegram digest is sent — so CI
  reports the run as failed rather than silently incomplete.
- **`send_digest` never raises**: a Telegram outage or bad token is logged
  and swallowed, so a notification failure never undoes a successful sheet
  write.

## Other things worth knowing

- **Region classification is a deliberate, narrow PAC list** — Singapore,
  Hong Kong, Australia, Japan, South Korea, China, Taiwan, New Zealand,
  Vietnam. Malaysia/Indonesia/Thailand/Philippines/Cambodia are excluded on
  purpose (despite e.g. Grab posting heavily there) to keep the PAC tabs
  scoped to today's audience rather than widen silently.
- **Location strings are free text, never split on commas** — offices are
  read from each API's real per-office array (Greenhouse `offices[]`,
  Lever `categories.allLocations`, etc.), normalized individually, deduped,
  then joined with `" | "`.
- **The sheet is the source of truth, not a database** — dedup/prune both
  read the *live* header row by column name, not a hardcoded position,
  which makes the column order a real migration concern (see
  `sheets/backfill.py`) if it's ever changed.
- **JD text is fetched for exactly two firms** — most employer apply pages
  show the JD fine; two firms' pages don't (one hides it behind
  client-side hydration, the other redirects to a generic listing), so
  only those two get their full description scraped into the sheet.
- **The Telegram digest is intentionally light** — grouped counts per
  (category, firm, location), not per-job listings with apply links, so
  the reader has a reason to open the sheet instead of just the message.
