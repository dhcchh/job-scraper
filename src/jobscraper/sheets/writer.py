"""Google Sheets access, formatting, append, and pruning helpers."""

import json
import logging

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

from jobscraper import config
from jobscraper.models import FIELDS, Job

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = list(FIELDS)


def _check_expected_title(spreadsheet: gspread.Spreadsheet) -> None:
    """Reject a spreadsheet whose configured title does not match."""
    if config.GOOGLE_SHEET_TITLE and spreadsheet.title != config.GOOGLE_SHEET_TITLE:
        raise RuntimeError(
            f"GOOGLE_SHEET_ID resolved to spreadsheet {spreadsheet.title!r}, "
            f"but GOOGLE_SHEET_TITLE expects {config.GOOGLE_SHEET_TITLE!r} -- "
            "refusing to write to what looks like the wrong spreadsheet"
        )


def get_spreadsheet() -> gspread.Spreadsheet:
    """Open and validate the configured spreadsheet."""
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    _check_expected_title(spreadsheet)
    return spreadsheet


def get_worksheet(spreadsheet: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
    """Return a named worksheet."""
    return spreadsheet.worksheet(name)


def format_date_columns(worksheet: gspread.Worksheet) -> None:
    """Format the managed date columns as `yyyy-mm-dd`."""
    if hasattr(worksheet, "format"):
        worksheet.format("A2:B", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})
        worksheet.format("L2:L", {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}})


def format_description_column(worksheet: gspread.Worksheet) -> None:
    """Clip the description column to preserve row height."""
    if hasattr(worksheet, "format"):
        worksheet.format("N2:N", {"wrapStrategy": "CLIP"})


def set_basic_filter(worksheet: gspread.Worksheet) -> None:
    """Apply a basic filter over all managed columns."""
    if hasattr(worksheet, "set_basic_filter"):
        last_column = rowcol_to_a1(1, len(FIELDS))[:-1]
        worksheet.set_basic_filter(f"A1:{last_column}")
        _resize_saved_filter_views(worksheet)


def _resize_saved_filter_views(worksheet: gspread.Worksheet) -> None:
    """Extend saved filter views across the managed columns and rows."""
    if not hasattr(worksheet, "client") or not hasattr(worksheet, "spreadsheet_id"):
        return

    try:
        metadata = worksheet.client.fetch_sheet_metadata(
            worksheet.spreadsheet_id, params={"includeGridData": "false"}
        )
        sheet = next(
            item
            for item in metadata["sheets"]
            if item["properties"]["sheetId"] == worksheet.id
        )
        views = sheet.get("filterViews", [])
        if not views:
            return

        grid_range = {
            "sheetId": worksheet.id,
            "startRowIndex": 0,
            "startColumnIndex": 0,
            "endColumnIndex": len(FIELDS),
        }
        requests = [
            {
                "updateFilterView": {
                    "filter": {
                        "filterViewId": view["filterViewId"],
                        "range": grid_range,
                    },
                    "fields": "range",
                }
            }
            for view in views
        ]
        worksheet.client.batch_update(worksheet.spreadsheet_id, {"requests": requests})
    except Exception:
        logger.warning(
            "couldn't resize saved filter views for sheet id %s", worksheet.id,
            exc_info=True,
        )


def append_jobs(worksheet: gspread.Worksheet, jobs: list[Job]) -> list[Job]:
    """Append jobs not already tracked by `(board, job_id)`."""
    if not jobs:
        logger.info("no jobs to append")
        return []

    existing = _existing_keys(worksheet)
    new_jobs = [job for job in jobs if (job.board, job.job_id) not in existing]
    skipped = len(jobs) - len(new_jobs)

    if not new_jobs:
        logger.info("no new jobs to append (%d already tracked)", skipped)
        return []

    rows = [job.to_row() for job in new_jobs]
    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    set_basic_filter(worksheet)
    logger.info(
        "appended %d new row(s), skipped %d already-tracked", len(rows), skipped
    )
    return new_jobs


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
    """Delete rows confirmed absent from successfully fetched boards."""
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

    closed_row_indexes = [
        row_index
        for row_index, row in enumerate(data_rows, start=1)
        if is_closed(row)
    ]
    pruned_count = len(closed_row_indexes)
    if pruned_count == 0:
        logger.info("prune: no closed postings")
        return 0

    # Delete bottom-up so one request cannot shift a later row index.
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
    set_basic_filter(worksheet)
    logger.info("prune: removed %d closed posting(s)", pruned_count)
    return pruned_count


def _row_key(
    row: list[str], board_index: int, job_id_index: int
) -> tuple[str, str] | None:
    """Read a row's `(board, job_id)`, returning None for a short row."""
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
