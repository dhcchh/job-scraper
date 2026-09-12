"""Google Sheets writer: append this run's jobs, prune anything stale.

Uses gspread (+ google-auth for the service-account credential) rather than
raw google-api-python-client -- gspread's
get_all_values()/append_rows()/update() map directly onto "append rows, read
rows, rewrite rows", with none of the discovery-service boilerplate the raw
client needs for the same three operations.

Design choice -- pruning rewrites the whole sheet in ONE clear()+update()
call, rather than issuing one delete_rows() call per stale row: the Sheets
API rate-limits per minute, a tracking sheet can accumulate hundreds of rows
over 60 days, and one call per stale row risks 429s where one bulk rewrite
doesn't.

Trade-off worth knowing: clear() and update() are two separate API calls,
not one atomic transaction -- a failure between them would leave the sheet
with just the header for a moment. Acceptable for a personal daily job; not
for anything with concurrent writers.
"""

from __future__ import annotations

import datetime
import json
import logging

import gspread
from google.oauth2.service_account import Credentials

from jobscraper import config
from jobscraper.models import FIELDS, Job

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = list(FIELDS)


def get_worksheet() -> gspread.Worksheet:
    """An authorized handle to the configured worksheet.

    TODO(extension point): assumes the worksheet tab already exists in the
    target spreadsheet -- add .add_worksheet(...) here to create it on first
    run instead of raising gspread.WorksheetNotFound.
    """
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
    return spreadsheet.worksheet(config.GOOGLE_SHEET_WORKSHEET_NAME)


def append_jobs(worksheet: gspread.Worksheet, jobs: list[Job]) -> None:
    """Append this run's jobs as new rows below whatever is already there."""
    if not jobs:
        logger.info("no jobs to append")
        return
    rows = [job.to_row() for job in jobs]
    worksheet.append_rows(rows, value_input_option="RAW")
    logger.info("appended %d rows", len(rows))


def prune_old_rows(
    worksheet: gspread.Worksheet,
    *,
    older_than_days: int = 60,
    today: datetime.date | None = None,
) -> None:
    """Delete every row whose scraped_date is older than `older_than_days`.

    `today` defaults to the real current date; overridable so tests don't
    depend on the wall clock.
    """
    if today is None:
        today = datetime.date.today()

    all_values = worksheet.get_all_values()
    if not all_values:
        return  # empty sheet -- nothing to prune, no header to preserve either

    header, *data_rows = all_values
    scraped_date_index = _column_index(header, "scraped_date")
    cutoff = today - datetime.timedelta(days=older_than_days)

    surviving = [
        row
        for row in data_rows
        if _row_scraped_date(row, scraped_date_index) >= cutoff
    ]

    pruned_count = len(data_rows) - len(surviving)
    if pruned_count == 0:
        logger.info("prune: nothing older than %s", cutoff.isoformat())
        return

    # Explicit keyword args here on purpose: gspread has changed
    # Worksheet.update's positional argument order (range_name vs values
    # first) across versions -- keywords keep this call correct regardless
    # of the exact version installed.
    worksheet.clear()
    worksheet.update(values=[header] + surviving, value_input_option="RAW")
    logger.info(
        "prune: removed %d rows older than %s", pruned_count, cutoff.isoformat()
    )


def _column_index(header: list[str], column_name: str) -> int:
    try:
        return header.index(column_name)
    except ValueError:
        raise ValueError(
            f"expected a {column_name!r} column in the sheet header, got {header!r}"
        ) from None


def _row_scraped_date(row: list[str], index: int) -> datetime.date:
    """A row's scraped_date, tolerant of short/malformed rows.

    A row that can't be parsed is treated as "very old" (dropped on the next
    prune) rather than raising and aborting the whole run.
    TODO: if silently dropping malformed rows turns out to be the wrong
    default (e.g. you'd rather keep and investigate), flip this to keep.
    """
    try:
        return datetime.date.fromisoformat(row[index])
    except (IndexError, ValueError):
        return datetime.date.min
