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

    TODO(retries): main.py currently aborts the whole run on any board's
    fetch failure, so reaching this call always means every configured
    board succeeded -- passing every configured board as fetched_boards is
    safe today. If per-board error handling is added later, pass only the
    boards that actually succeeded this run, or a board that failed without
    raising would have its still-open postings deleted here.
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
