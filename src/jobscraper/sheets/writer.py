"""Google Sheets access, formatting, append, and pruning helpers."""

import dataclasses
import json
import logging
import time
from collections import Counter
from collections.abc import Iterable, Mapping

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

from jobscraper import config
from jobscraper.models import DATE_FIELDS, FIELDS, Job

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = list(FIELDS)
_BOARD_INDEX = HEADER.index("board")
_JOB_ID_INDEX = HEADER.index("job_id")

# A board that "succeeds" but has quietly broken (a WAF page parsed as an
# empty list, a pagination or job_id change) looks just like a firm closing
# most of its postings at once, so pruning that much needs a human's say-so.
PRUNE_GUARD_MAX_FRACTION = 0.5
PRUNE_GUARD_MIN_ROWS = 5


def _check_expected_title(spreadsheet: gspread.Spreadsheet) -> None:
    """Reject a spreadsheet whose configured title does not match."""
    if config.GOOGLE_SHEET_TITLE and spreadsheet.title != config.GOOGLE_SHEET_TITLE:
        raise RuntimeError(
            f"GOOGLE_SHEET_ID resolved to spreadsheet {spreadsheet.title!r}, "
            f"but GOOGLE_SHEET_TITLE expects {config.GOOGLE_SHEET_TITLE!r} -- "
            "refusing to write to what looks like the wrong spreadsheet"
        )


# Seconds to wait before each retry of a request Sheets rejected for quota.
_QUOTA_RETRY_WAITS = (10, 20, 40, 60)


class _QuotaRetryingHTTPClient(gspread.HTTPClient):
    """Retry a request Sheets rejected with HTTP 429 (per-minute quota).

    A 429 means the request wasn't applied, so retrying is safe even for an
    append. A full refresh makes ~7 writes per tab, which exceeded the
    60-writes-per-minute quota on 2026-09-27. Unlike gspread's own
    BackOffHTTPClient, 5xx errors are not retried: a failed append may
    already have landed, and repeating it would duplicate rows.
    """

    def request(self, *args, **kwargs):
        for wait in _QUOTA_RETRY_WAITS:
            try:
                return super().request(*args, **kwargs)
            except APIError as err:
                if err.code != 429:
                    raise
                logger.warning("sheets write quota hit, retrying in %ds", wait)
                time.sleep(wait)
        return super().request(*args, **kwargs)


def get_spreadsheet() -> gspread.Spreadsheet:
    """Open and validate the configured spreadsheet."""
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(credentials, http_client=_QuotaRetryingHTTPClient)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    _check_expected_title(spreadsheet)
    return spreadsheet


def get_worksheet(spreadsheet: gspread.Spreadsheet, name: str) -> gspread.Worksheet:
    """Return a named worksheet."""
    return spreadsheet.worksheet(name)


_DATE_FORMAT = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}


def format_tab(worksheet: gspread.Worksheet) -> None:
    """Apply every managed format to a tab in one write.

    That's `yyyy-mm-dd` date columns, a clipped description column (so a
    long JD doesn't blow up its row's height), and the basic filter. One
    batch rather than a call each keeps a ten-tab refresh well under Sheets'
    60-writes-per-minute quota.
    """
    if not _has_client(worksheet):
        return
    requests = [
        *(_repeat_cell(worksheet, name, _DATE_FORMAT) for name in DATE_FIELDS),
        _repeat_cell(worksheet, "description", {"wrapStrategy": "CLIP"}),
        *_filter_requests(worksheet),
    ]
    worksheet.client.batch_update(worksheet.spreadsheet_id, {"requests": requests})


def set_basic_filter(worksheet: gspread.Worksheet) -> None:
    """Apply a basic filter over all managed columns, in one write."""
    if not _has_client(worksheet):
        return
    worksheet.client.batch_update(
        worksheet.spreadsheet_id, {"requests": _filter_requests(worksheet)}
    )


def _has_client(worksheet: gspread.Worksheet) -> bool:
    return hasattr(worksheet, "client") and hasattr(worksheet, "spreadsheet_id")


def _repeat_cell(worksheet: gspread.Worksheet, field: str, cell_format: dict) -> dict:
    """Format one managed column below the header, wherever `FIELDS` puts it."""
    column = FIELDS.index(field)
    return {
        "repeatCell": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": 1,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            },
            "cell": {"userEnteredFormat": cell_format},
            "fields": "userEnteredFormat(%s)" % ",".join(cell_format),
        }
    }


def _filter_requests(worksheet: gspread.Worksheet) -> list[dict]:
    """Set the basic filter, and stretch saved filter views to match it."""
    grid_range = {
        "sheetId": worksheet.id,
        "startRowIndex": 0,
        "startColumnIndex": 0,
        "endColumnIndex": len(FIELDS),
    }
    return [
        {"setBasicFilter": {"filter": {"range": grid_range}}},
        *(
            {
                "updateFilterView": {
                    "filter": {"filterViewId": view_id, "range": grid_range},
                    "fields": "range",
                }
            }
            for view_id in _saved_filter_view_ids(worksheet)
        ),
    ]


def _saved_filter_view_ids(worksheet: gspread.Worksheet) -> list[int]:
    """Ids of the tab's saved filter views, or none if they can't be read."""
    try:
        metadata = worksheet.client.fetch_sheet_metadata(
            worksheet.spreadsheet_id, params={"includeGridData": "false"}
        )
        sheet = next(
            item
            for item in metadata["sheets"]
            if item["properties"]["sheetId"] == worksheet.id
        )
    except Exception:
        logger.warning(
            "couldn't read saved filter views for sheet id %s", worksheet.id,
            exc_info=True,
        )
        return []
    return [view["filterViewId"] for view in sheet.get("filterViews", [])]


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
    _check_header(worksheet, header)
    keys = set()
    for row in data_rows:
        key = _row_key(row)
        if key is not None:
            keys.add(key)
    return keys


@dataclasses.dataclass(frozen=True, slots=True)
class PrunePlan:
    """One tab's closed rows, found before any tab is pruned."""

    worksheet: gspread.Worksheet
    closed_rows: dict[str, list[int]]  # board -> 1-based data row indexes
    tracked_counts: Counter[str]  # board -> rows in the tab, closed or not


def plan_prune(
    worksheet: gspread.Worksheet,
    current_jobs: list[Job],
    fetched_boards: set[str],
) -> PrunePlan:
    """Find rows confirmed absent from successfully fetched boards."""
    closed_rows: dict[str, list[int]] = {}
    tracked_counts: Counter[str] = Counter()
    all_values = worksheet.get_all_values()
    if not all_values:
        return PrunePlan(worksheet, closed_rows, tracked_counts)

    header, *data_rows = all_values
    _check_header(worksheet, header)
    current_keys = {(job.board, job.job_id) for job in current_jobs}
    for row_index, row in enumerate(data_rows, start=1):
        key = _row_key(row)
        if key is None:
            continue  # can't confirm closure -- leave it alone
        board, _job_id = key
        tracked_counts[board] += 1
        if board in fetched_boards and key not in current_keys:
            closed_rows.setdefault(board, []).append(row_index)
    return PrunePlan(worksheet, closed_rows, tracked_counts)


def held_back_prunes(
    plans: Iterable[PrunePlan],
    fetched_counts: Mapping[str, int],
    appended_counts: Mapping[str, int],
) -> dict[str, str]:
    """Return firm -> reason for each firm whose prune looks like a broken fetch.

    Counts are summed across every tab. Rows appended this run are left out
    of a firm's tracked total, so a job_id format change (every old row
    closed, every posting re-appended under a new id) still reads as 100%.
    """
    closed: Counter[str] = Counter()
    tracked: Counter[str] = Counter()
    for plan in plans:
        for board, rows in plan.closed_rows.items():
            closed[board] += len(rows)
        tracked.update(plan.tracked_counts)

    held_back = {}
    for firm, closed_count in sorted(closed.items()):
        previously_tracked = tracked[firm] - appended_counts.get(firm, 0)
        if fetched_counts.get(firm, 0) == 0:
            held_back[firm] = (
                f"board returned 0 postings; would prune all {closed_count} "
                "tracked row(s)"
            )
        elif (
            closed_count >= PRUNE_GUARD_MIN_ROWS
            and closed_count > PRUNE_GUARD_MAX_FRACTION * previously_tracked
        ):
            held_back[firm] = (
                f"would prune {closed_count} of {previously_tracked} tracked row(s)"
            )
    return held_back


def apply_prune(plan: PrunePlan, held_back: Iterable[str] = ()) -> int:
    """Delete a plan's closed rows, except those of held-back firms."""
    held_back = set(held_back)
    closed_row_indexes = sorted(
        row_index
        for board, row_indexes in plan.closed_rows.items()
        if board not in held_back
        for row_index in row_indexes
    )
    pruned_count = len(closed_row_indexes)
    if pruned_count == 0:
        logger.info("prune: no closed postings")
        return 0

    worksheet = plan.worksheet
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


def _check_header(worksheet: gspread.Worksheet, header: list[str]) -> None:
    """Refuse a tab whose header disagrees with this code's `FIELDS`.

    Rows are written and read by position, so a tab migrated by code this
    checkout doesn't have would get shifted rows -- the 2026-09-21 incident
    corrupted job_id this way, and prune then deleted the corrupted rows.
    Extra columns after `FIELDS` are left alone and tolerated.
    """
    if header[: len(HEADER)] != HEADER:
        raise RuntimeError(
            f"{worksheet.title!r}: sheet header {header!r} doesn't start with "
            f"this code's FIELDS {HEADER!r} -- refusing to append or prune. "
            "The sheet was likely migrated by code this checkout doesn't have "
            "(or vice versa): push that code, or rerun sheets.backfill from a "
            "pushed checkout."
        )


def _row_key(row: list[str]) -> tuple[str, str] | None:
    """Read a row's `(board, job_id)`, returning None for a short row."""
    try:
        return (row[_BOARD_INDEX], row[_JOB_ID_INDEX])
    except IndexError:
        return None
