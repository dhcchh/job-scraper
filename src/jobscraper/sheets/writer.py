"""Google Sheets writer: append not-yet-seen jobs, prune closed postings.

Uses gspread (+ google-auth for the service-account credential) rather than
raw google-api-python-client -- gspread's
get_all_values()/append_rows()/update() map directly onto "append rows, read
rows, rewrite rows", with none of the discovery-service boilerplate the raw
client needs for the same three operations.

A posting is tracked by (board, job_id): append_jobs skips one already in
the sheet instead of adding a duplicate row for it, and prune_closed_postings
removes one once it's no longer in the board's current fetch -- i.e. it's
actually closed, not just old. Neither function reasons about age; how long
a posting has stayed open is left in scraped_date for a human to judge (some
firms auto-reject on a rolling basis well before they take a listing down,
which the Greenhouse API has no way to expose).

Design choice -- pruning rewrites the whole sheet in ONE clear()+update()
call, rather than issuing one delete_rows() call per closed posting: the
Sheets API rate-limits per minute, a tracking sheet can accumulate hundreds
of rows, and one call per row risks 429s where one bulk rewrite doesn't.

Trade-off worth knowing: clear() and update() are two separate API calls,
not one atomic transaction -- a failure between them would leave the sheet
with just the header for a moment. Acceptable for a personal daily job; not
for anything with concurrent writers.
"""

from __future__ import annotations

import json
import logging

import gspread
from google.oauth2.service_account import Credentials

from jobscraper import config
from jobscraper.models import FIELDS, Job

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = list(FIELDS)


def get_spreadsheet() -> gspread.Spreadsheet:
    """An authorized handle to the configured spreadsheet.

    Opened once per run and shared across every category's get_worksheet()
    call below, rather than re-authorizing per tab.
    """
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(credentials)
    return client.open_by_key(config.GOOGLE_SHEET_ID)


def get_worksheet(spreadsheet: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
    """One tab of the spreadsheet, named for a jobscraper.classify category.

    TODO(extension point): assumes the tab already exists -- add
    spreadsheet.add_worksheet(...) here to create a new category's tab
    automatically instead of raising gspread.WorksheetNotFound.
    """
    return spreadsheet.worksheet(name)


def append_jobs(worksheet: gspread.Worksheet, jobs: list[Job]) -> None:
    """Append this run's not-yet-seen jobs as new rows.

    A job already present in the sheet -- matched by (board, job_id) -- is
    skipped rather than appended again. Without this, a still-open posting
    would get a fresh row every single day it's fetched. A side effect
    worth knowing: since a job's row is now written exactly once, its
    scraped_date is effectively "first seen", not "last confirmed open".
    """
    if not jobs:
        logger.info("no jobs to append")
        return

    existing = _existing_keys(worksheet)
    new_jobs = [job for job in jobs if (job.board, job.job_id) not in existing]
    skipped = len(jobs) - len(new_jobs)

    if not new_jobs:
        logger.info("no new jobs to append (%d already tracked)", skipped)
        return

    rows = [job.to_row() for job in new_jobs]
    worksheet.append_rows(rows, value_input_option="RAW")
    logger.info(
        "appended %d new row(s), skipped %d already-tracked", len(rows), skipped
    )


def _existing_keys(worksheet: gspread.Worksheet) -> set[tuple[str, str]]:
    """(board, job_id) of every row already in the sheet."""
    all_values = worksheet.get_all_values()
    if not all_values:
        return set()

    header, *data_rows = all_values
    board_index = _column_index(header, "board")
    job_id_index = _column_index(header, "job_id")

    keys = set()
    for row in data_rows:
        key = _row_key(row, board_index, job_id_index)
        if key is not None:
            keys.add(key)
    return keys


def prune_closed_postings(
    worksheet: gspread.Worksheet,
    current_jobs: list[Job],
    fetched_boards: set[str],
) -> None:
    """Delete every row for a posting that's no longer open.

    A row is closed -- and removed -- when its board is in fetched_boards
    (we successfully checked that board this run) but its (board, job_id)
    isn't among current_jobs, meaning Greenhouse no longer lists it. This is
    the actual "still hiring?" signal: a posting that's still open just
    never gets touched here, no matter how long it's been up.

    fetched_boards must be passed explicitly rather than inferred from
    current_jobs' own boards: a board with zero *currently* open postings
    would otherwise be invisible in current_jobs and look unfetched, so its
    (actually closed) rows would wrongly survive.

    main.py passes only the firms whose fetch actually succeeded this run
    (and, for a firm with more than one board token, only if ALL of its
    tokens succeeded) -- a board that failed after retries is left out, so
    its still-open postings are correctly left alone rather than deleted.
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        return  # empty sheet -- nothing to prune, no header to preserve either

    header, *data_rows = all_values
    board_index = _column_index(header, "board")
    job_id_index = _column_index(header, "job_id")
    current_keys = {(job.board, job.job_id) for job in current_jobs}

    def is_closed(row: list[str]) -> bool:
        key = _row_key(row, board_index, job_id_index)
        if key is None:
            return False  # can't confirm closure -- leave it alone
        board, _job_id = key
        return board in fetched_boards and key not in current_keys

    surviving = [row for row in data_rows if not is_closed(row)]

    pruned_count = len(data_rows) - len(surviving)
    if pruned_count == 0:
        logger.info("prune: no closed postings")
        return

    # Explicit keyword args here on purpose: gspread has changed
    # Worksheet.update's positional argument order (range_name vs values
    # first) across versions -- keywords keep this call correct regardless
    # of the exact version installed.
    worksheet.clear()
    worksheet.update(values=[header] + surviving, value_input_option="RAW")
    logger.info("prune: removed %d closed posting(s)", pruned_count)


def sort_rows(worksheet: gspread.Worksheet) -> None:
    """Sort every data row: most recent scraped_date first, then location,
    then company_type, then board (firm name) -- alphabetically within each.

    Only needs calling after append_jobs actually added rows -- those land at
    the bottom, unsorted relative to what's already there. prune_closed_postings
    only ever removes rows, which can't un-sort a list that was already
    sorted, so there's no need to re-sort after every prune too (this
    function is cheap to call unconditionally anyway: it no-ops if the sheet
    is already in order).

    scraped_date is stored as an ISO "YYYY-MM-DD" string, which happens to
    sort correctly as plain text in the same order as chronologically, so
    "most recent first" is just reverse alphabetical on that column -- no
    date parsing needed.

    Implemented as four separate stable sorts, least significant key first
    (board, then company_type, then location, then scraped_date last) rather
    than one sort with a combined key -- Python's sort() is guaranteed
    stable, so this composes correctly into "recency, then location, then
    company_type, then board" and reads as plainly as the four-level
    ordering it produces.
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        return

    header, *data_rows = all_values
    board_index = _column_index(header, "board")
    company_type_index = _column_index(header, "company_type")
    location_index = _column_index(header, "location")
    scraped_date_index = _column_index(header, "scraped_date")

    sorted_rows = list(data_rows)
    # .strip().lower() on board/company_type/location: .lower() so e.g.
    # "APAC" and "Aarhus" sort as a human would expect -- plain string
    # comparison would put "APAC" first, since uppercase letters sort
    # before lowercase ones in ASCII -- and .strip() because Greenhouse's
    # own multi-location strings sometimes carry stray leading/trailing
    # whitespace (e.g. " London, Montreal, Singapore"), which would
    # otherwise sort before every letter (a space's ASCII value is lower
    # than any letter's) and bump that row to the top of its group instead
    # of alphabetizing it under "L". scraped_date doesn't need either: it's
    # a fixed-format "YYYY-MM-DD" string, no letters or stray whitespace.
    sorted_rows.sort(key=lambda row: _safe_value(row, board_index).strip().lower())
    sorted_rows.sort(key=lambda row: _safe_value(row, company_type_index).strip().lower())
    sorted_rows.sort(key=lambda row: _safe_value(row, location_index).strip().lower())
    sorted_rows.sort(key=lambda row: _safe_value(row, scraped_date_index), reverse=True)

    if sorted_rows == data_rows:
        logger.info("sort: already in order")
        return

    worksheet.update(values=[header] + sorted_rows, value_input_option="RAW")
    logger.info("sort: reordered %d row(s)", len(sorted_rows))


def _safe_value(row: list[str], index: int) -> str:
    """row[index], or "" if the row is too short to have it.

    A malformed row shouldn't crash a sort -- "" sorts first ascending (board,
    location) and last on the reversed scraped_date sort, i.e. to the bottom
    either way, a safe default for a row we can't fully read.
    """
    return row[index] if index < len(row) else ""


def _row_key(
    row: list[str], board_index: int, job_id_index: int
) -> tuple[str, str] | None:
    """A row's (board, job_id), or None if the row is too short to read.

    None is treated as "not already tracked" by _existing_keys (so it can't
    block a legitimate append) and as "not closed" by prune_closed_postings
    (so a parsing hiccup never causes a delete) -- callers should never
    guess at a value for a malformed row, only skip it.
    """
    try:
        return (row[board_index], row[job_id_index])
    except IndexError:
        return None


def _column_index(header: list[str], column_name: str) -> int:
    try:
        return header.index(column_name)
    except ValueError:
        raise ValueError(
            f"expected a {column_name!r} column in the sheet header, got {header!r}"
        ) from None
