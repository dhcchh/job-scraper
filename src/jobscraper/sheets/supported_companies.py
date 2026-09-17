"""Supported-companies reference tab: every firm this project scrapes,
grouped by company_type -- kept in sync with the checked-in
company_toml/*_boards.toml files rather than maintained by hand.

Run standalone to (re)populate the tab immediately --

    uv run python -m jobscraper.sheets.supported_companies

-- and also called from main.run() at the end of every daily scrape, so a
firm added to any of the five *_boards.toml files shows up here
automatically on the very next run, with no separate manual step (unlike
sheets/backfill.py and sheets/full_refresh.py, which are manual-only). This
is safe to run unattended because it reasons about nothing but the current
config -- no in-flight job data, no existing-row state to merge with -- so
there's no interrupted-partway-through state it could ever leave stranded.

The tab itself is user-created (see CLAUDE.md) rather than one of the ten
(category, region) job tabs main.py already assumes exist, so
_get_or_create_worksheet matches TAB_NAME case/whitespace-insensitively
against the spreadsheet's existing tabs before falling back to creating one
-- a brand new spreadsheet (or a renamed tab) still gets a usable tab
instead of sheets.writer.get_worksheet's usual WorksheetNotFound.
"""

from __future__ import annotations

import logging

import gspread

from jobscraper import config
from jobscraper.sheets.writer import get_spreadsheet, set_basic_filter

logger = logging.getLogger(__name__)

TAB_NAME = "Supported companies"
HEADER = ["Category", "Company"]


def build_rows() -> list[list[str]]:
    """[category, firm] for every configured firm, deduped by firm and
    sorted by (category, firm).

    A firm with more than one board token (e.g. Optiver has two Greenhouse
    tokens) is one row here, not one per token -- this tab answers "which
    firms are supported", not "which board tokens exist". The five
    *_boards.toml files are walked in the same order main.py's
    board_sources fetch loop uses; a firm's first occurrence wins in the
    unexpected case it were ever listed with two different company_type
    values, since a firm is only ever meant to be configured once, in one
    file, under one category.
    """
    all_boards = (
        config.GREENHOUSE_BOARDS
        + config.LEVER_BOARDS
        + config.ASHBY_BOARDS
        + config.SMARTRECRUITERS_BOARDS
        + config.EIGHTFOLD_BOARDS
    )
    category_by_firm: dict[str, str] = {}
    for board in all_boards:
        category_by_firm.setdefault(board["firm"], board["company_type"])

    return sorted(
        ([category, firm] for firm, category in category_by_firm.items()),
        key=lambda row: (row[0], row[1]),
    )


def _get_or_create_worksheet(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    target = TAB_NAME.strip().casefold()
    for worksheet in spreadsheet.worksheets():
        if worksheet.title.strip().casefold() == target:
            return worksheet
    return spreadsheet.add_worksheet(title=TAB_NAME, rows=100, cols=len(HEADER))


def sync_supported_companies(spreadsheet: gspread.Spreadsheet) -> int:
    """Rewrite the supported-companies tab from current config, from scratch.

    Cleared first, unlike sheets/writer.append_jobs' incremental append or
    sheets/backfill.py's in-place same-row-count rewrite: this tab's row
    count tracks the number of currently configured firms directly, so a
    firm removed from every *_boards.toml file must disappear from the tab
    too, which an in-place update() over a shrinking range wouldn't do on
    its own.

    Returns the number of company rows written (header excluded).
    """
    worksheet = _get_or_create_worksheet(spreadsheet)
    rows = build_rows()
    worksheet.clear()
    worksheet.update(
        range_name="A1", values=[HEADER] + rows, value_input_option="USER_ENTERED"
    )
    set_basic_filter(worksheet)
    logger.info("supported companies: wrote %d firm(s)", len(rows))
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sync_supported_companies(get_spreadsheet())
