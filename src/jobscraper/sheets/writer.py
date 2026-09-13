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

Pruning uses one `spreadsheets.batchUpdate` request containing a
`deleteDimension` request per closed row. Google applies that batch
atomically: a failure leaves the worksheet untouched. Deleting only the
closed rows also preserves formulas and user-managed columns instead of
reconstructing the entire worksheet from displayed values.
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


def _check_expected_title(spreadsheet: gspread.Spreadsheet) -> None:
    """Fail fast if GOOGLE_SHEET_TITLE is set and doesn't match.

    GOOGLE_SHEET_ID is set independently in .env (local) and the GitHub
    Actions secret (CI); nothing else cross-checks the two, so a value that
    drifts on one side would otherwise write/prune real data against the
    wrong file while still reporting a clean run. This is an opt-in second
    check -- skipped entirely when GOOGLE_SHEET_TITLE isn't set.
    """
    if config.GOOGLE_SHEET_TITLE and spreadsheet.title != config.GOOGLE_SHEET_TITLE:
        raise RuntimeError(
            f"GOOGLE_SHEET_ID resolved to spreadsheet {spreadsheet.title!r}, "
            f"but GOOGLE_SHEET_TITLE expects {config.GOOGLE_SHEET_TITLE!r} -- "
            "refusing to write to what looks like the wrong spreadsheet"
        )


def get_spreadsheet() -> gspread.Spreadsheet:
    """An authorized handle to the configured spreadsheet.

    Opened once per run and shared across every category's get_worksheet()
    call below, rather than re-authorizing per tab. See _check_expected_title
    for the optional GOOGLE_SHEET_TITLE drift check applied before returning.
    """
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    _check_expected_title(spreadsheet)
    return spreadsheet


def get_worksheet(spreadsheet: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
    """One tab of the spreadsheet, named for a jobscraper.classify category.

    TODO(extension point): assumes the tab already exists -- add
    spreadsheet.add_worksheet(...) here to create a new category's tab
    automatically instead of raising gspread.WorksheetNotFound.
    """
    return spreadsheet.worksheet(name)


def format_date_columns(worksheet: gspread.Worksheet) -> None:
    """Explicitly format date columns as DATE ('yyyy-mm-dd') in Google Sheets.

    Applies to first_published (D), updated_at (E), and scraped_date (K)
    data cells so Google Sheets displays them uniformly.
    """
    if hasattr(worksheet, "format"):
        worksheet.format("D2:E", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
        worksheet.format("K2:K", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})


def append_jobs(worksheet: gspread.Worksheet, jobs: list[Job]) -> int:
    """Append this run's not-yet-seen jobs as new rows.

    A job already present in the sheet -- matched by (board, job_id) -- is
    skipped rather than appended again. Without this, a still-open posting
    would get a fresh row every single day it's fetched. A side effect
    worth knowing: since a job's row is now written exactly once, its
    scraped_date is effectively "first seen", not "last confirmed open".

    Returns the number of rows actually appended (0 if none) -- main.py
    rolls this up into its per-run summary.
    """
    if not jobs:
        logger.info("no jobs to append")
        return 0

    existing = _existing_keys(worksheet)
    new_jobs = [job for job in jobs if (job.board, job.job_id) not in existing]
    skipped = len(jobs) - len(new_jobs)

    if not new_jobs:
        logger.info("no new jobs to append (%d already tracked)", skipped)
        return 0

    rows = [job.to_row() for job in new_jobs]
    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(
        "appended %d new row(s), skipped %d already-tracked", len(rows), skipped
    )
    return len(rows)


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
) -> int:
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

    Returns the number of rows actually removed (0 if none) -- main.py
    rolls this up into its per-run summary.
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        return 0  # empty sheet -- nothing to prune, no header to preserve either

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

    # Sheet row 1 is the header. `startIndex` in a DeleteDimensionRequest is
    # zero-based, so the first data row has startIndex 1. Delete bottom-up:
    # each request is applied in sequence within the atomic batch and must not
    # shift the index of a row still waiting to be removed.
    closed_row_indexes = [
        row_index
        for row_index, row in enumerate(data_rows, start=1)
        if is_closed(row)
    ]
    pruned_count = len(closed_row_indexes)
    if pruned_count == 0:
        logger.info("prune: no closed postings")
        return 0

    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "ROWS",
                    "startIndex": row_index,
                    "endIndex": row_index + 1,
                }
            }
        }
        for row_index in reversed(closed_row_indexes)
    ]
    worksheet.client.batch_update(worksheet.spreadsheet_id, {"requests": requests})
    logger.info("prune: removed %d closed posting(s)", pruned_count)
    return pruned_count


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
