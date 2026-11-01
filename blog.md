# How the job scraper works

The scraper reads open postings from company career boards, keeps the roles it cares about, and files them in a Google Sheet by role and region. It can also post new ones to Telegram. There is no database. **The sheet is the only memory:** what has been seen, when it was first seen, and which firms were announced. Most decisions below follow from that.

## The daily run

1. **Open the sheet.** Stop if its title differs from `GOOGLE_SHEET_TITLE` (when set). Add newly configured firms to the Supported companies tab and announce them.
2. **Fetch every board.** Boards are fetched concurrently (8 workers, with Workday in its own pool of 4). Workday and Eightfold also fetch their pages, and Workday its detail records, with up to 8 workers. A failed board is fetched once more after 60 seconds.
3. **Classify.** A title keyword picks one of five roles, or the posting is noise. Drop it if every office is in an excluded country. The rest go to PAC or RoW. Role and region name one of ten tabs.
4. **Plan.** Read every tab, check headers, find rows no longer listed, run the prune guard.
5. **Write.** Append unseen postings. Re-read tabs that grew, since a saved sort can reorder rows. Delete closed rows. Held-back firms skip both.
6. **Report.** Write the run summary, send Telegram, then fail the run if any board failed or any firm was held back.

## Decisions, and why

**A posting is (firm, job ID).** The firm name sits in the `board` column. IDs are only unique within a provider, and titles and locations change, so the ID is the one field trusted to stay put. Renaming a firm changes every key (see Known gap).

**Closed postings are deleted, not marked closed.** The tabs show only what is open now. The cost is history: a returning posting looks new.

**A firm is refreshed whole or not at all.** Rows carry no token, so a row cannot be traced to its board. If one of a firm's boards fails, its rows would look closed. Nothing is pruned unless every board succeeded.

**A bad response fails the board.** A short page, an empty list or a missing ID looks just like "everything closed". Each fetch checks page counts against the reported total and needs a unique ID on every posting.

**Unsure means keep the row.** A stale row is clutter. A wrongly deleted one returns as a new, announced posting.

**Sheet writes are not retried; reads and 429s are.** A 429 is retried after 10, 20, 40, then 60 seconds. Reads also retry HTTP 500, 502, 503, 504 and dropped connections after 5, 15, then 30 seconds. Any other failed write may already have landed, and a retry could duplicate rows. The next run dedupes by key.

**Full Refresh is one atomic write.** Separate per-tab writes could lose rows if one failed midway. All rows and grid sizes go in one validated batch; formatting follows. Sheets documents batches as all-or-nothing; not tested live.

**Alerts are best effort.** Nothing records "not yet announced", so a failed send is never retried. Errors omit the request URL, which contains the bot token.

## What happens when things go wrong

| Situation | What happens |
| --- | --- |
| Timeout, connection error, 5xx or 429 from a board | Up to 3 attempts, waiting 2 s then 4 s. A 429's `Retry-After` seconds replace the wait (max 60 s). Requests time out at 30 s. Other 4xx fail at once. |
| A board still fails | Failed boards are refetched once, 60 s later (Daily Scrape only). If it fails again, the firm's rows stay and the run ends red after everything else finishes. Backfill and Full Refresh keep those rows' stored data. |
| Malformed data, missing or duplicate IDs | The board fails. A paged feed short of its total is retried whole once; parallel page fetches fall back to one at a time. |
| A firm has several boards, one fails | Nothing of that firm is pruned. Boards that worked still append. |
| Empty feed or mass drop | The prune guard holds the firm: no appends, no prunes, red run. It trips if the feed returned 0 postings (before role filtering) while rows are tracked, or if at least 5 rows and more than half of the firm's tracked rows (all tabs) would go. Exactly half passes. To proceed, rerun Daily Scrape with `force_prune_firms` (comma-separated), or set `FORCE_PRUNE_FIRMS` locally. A board stuck at 0 may have changed provider, which changes every ID: probe other providers, then run Full Refresh before scraping. |
| Same posting seen again, even with an edited title | Skipped. The row keeps its first-written title and dates until a refresh, unless the new title changes its tab (see below). |
| A posting closes, then reopens | Deleted on the next successful run, then appended as new with a fresh `scraped_date` and announced again. One fetch that misses it does the same. |
| A title or rule change moves a posting | The old tab's row looks closed and the new tab's key unseen: deleted there, appended as new here, and Telegram can announce it. A posting that becomes noise or fully excluded is deleted and counts toward the guard. Run Full Refresh first: it moves rows, keeps dates, and silently adds newly matching open postings. |
| Sheet write fails partway | The run stops. Earlier tabs keep their changes; no summary or notification goes out. The next run redoes the rest. Appended but unannounced rows stay silent. Backfill writes tab by tab, so rerun it. |
| Two writers at once | Actions queues the four sheet writers in one group. Local runs have no lock. Overlap is unsafe: prunes delete by row numbers read earlier, and Full Refresh rewrites whole tabs. From the code; not tested. |
| Header or tab mismatch | Each tab must exist and start with the current `FIELDS`, else the run stops before any append or prune (the Supported companies sync may already have run). Extra trailing columns are fine. Push the matching code, or rerun Backfill from a pushed checkout. |
| Full Refresh fails while formatting | The data is already committed; some tabs may lack date format, frozen header or filter. Rerun. Filter criteria and sort are kept; if unreadable, the update fails instead. |
| Telegram fails | Logged, never retried, run not failed. A failed chunk skips the rest of its message. A lost company announcement is not repeated: the tab already lists the firm. |
| Workday placeholders | Items with no title and no link count toward the total but are skipped. More than 3, and more than 5% of the listing, fails the board. A detail request (for multi-office postings) is retried; a 404 or 410 means the posting closed and it is omitted; any other failure fails the board. |

## Known gap: renaming a firm

Changing `firm` in a board file changes every posting's identity, and nothing migrates the sheet.

- **Old rows** keep the old name and are never pruned, because that name is never fetched again. `force_prune_firms` cannot reach them.
- **New postings** all look unseen. The next daily run appends and announces them. Supported companies announces the new name and keeps the old.
- **Full Refresh** helps halfway: it adds the new postings silently but keeps the old rows, so each appears twice.
- **Editing the old rows' `board` cell** to the new name works if done before the first scrape under it. Once duplicates exist, the audit flags them and Full Refresh keeps the first copy it meets.

Cheapest safe fix: before pushing the TOML change (a push triggers a scrape), rewrite that firm's `board` cells on every tab and its Supported companies cell. If the firm is in `FIRMS_NEEDING_DESCRIPTION`, rename it there too, or the next Backfill clears its descriptions. Not verified against a live sheet; no script or test covers it.

## Where the rules live

- `enrichment/classify_roles.py`, `classify_region.py`, `normalize_location.py`: role keywords, country sets, location cleanup.
- `sheets/writer.py`: prune guard thresholds, Sheets retry waits, header check.
- `job_boards/_http.py` and `fetchers.py`: board retries, pagination checks, worker counts.
- `models.py` and `main.py`: `FIELDS`, the description allowlist, the daily run's order.
