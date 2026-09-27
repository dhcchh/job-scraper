"""Sync configured firms to the supported-companies sheet tab."""

import logging
import re
from collections import defaultdict

import gspread

from jobscraper import config
from jobscraper.sheets.writer import get_spreadsheet
from jobscraper.telegram import send_new_companies

logger = logging.getLogger(__name__)

TAB_NAME = "Supported companies"


def _slugify(label: str) -> str:
    """Convert a company type to a snake_case column name."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _category_by_firm() -> dict[str, str]:
    """Map each configured firm to its company type."""
    all_boards = (
        config.GREENHOUSE_BOARDS
        + config.LEVER_BOARDS
        + config.ASHBY_BOARDS
        + config.SMARTRECRUITERS_BOARDS
        + config.EIGHTFOLD_BOARDS
        + config.WORKDAY_BOARDS
        + config.RECRUITEE_BOARDS
    )
    category_by_firm: dict[str, str] = {}
    for board in all_boards:
        category_by_firm.setdefault(board["firm"], board["company_type"])
    return category_by_firm


def build_columns() -> dict[str, list[str]]:
    """Group sorted, unique firm names by company-type slug."""
    firms_by_slug: dict[str, list[str]] = defaultdict(list)
    for firm, category in _category_by_firm().items():
        firms_by_slug[_slugify(category)].append(firm)

    return {slug: sorted(firms) for slug, firms in sorted(firms_by_slug.items())}


def _grid(columns: dict[str, list[str]]) -> list[list[str]]:
    """Build the supported-companies header and data rows."""
    headers = list(columns.keys())
    height = max((len(firms) for firms in columns.values()), default=0)
    rows = [headers]
    for i in range(height):
        rows.append([firms[i] if i < len(firms) else "" for firms in columns.values()])
    return rows


def _get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, cols: int) -> gspread.Worksheet:
    target = TAB_NAME.strip().casefold()
    for worksheet in spreadsheet.worksheets():
        if worksheet.title.strip().casefold() == target:
            return worksheet
    return spreadsheet.add_worksheet(title=TAB_NAME, rows=100, cols=max(cols, 1))


def _listed_firms(worksheet: gspread.Worksheet) -> set[str]:
    """Return every non-blank firm below the header."""
    rows = worksheet.get_all_values()
    return {cell.strip() for row in rows[1:] for cell in row if cell.strip()}


def sync_supported_companies(spreadsheet: gspread.Spreadsheet) -> dict[str, str]:
    """Rewrite the supported-companies tab and return newly added firms."""
    category_by_firm = _category_by_firm()
    columns = build_columns()
    worksheet = _get_or_create_worksheet(spreadsheet, cols=len(columns))
    previously_listed = _listed_firms(worksheet)
    worksheet.clear()
    if columns:
        worksheet.update(
            range_name="A1", values=_grid(columns), value_input_option="USER_ENTERED"
        )
    total = sum(len(firms) for firms in columns.values())
    logger.info(
        "supported companies: wrote %d firm(s) across %d categor(y/ies)",
        total,
        len(columns),
    )

    if not previously_listed:
        logger.info(
            "supported companies: tab listed no firms before this run, "
            "not treating any as newly added"
        )
        return {}
    new_firms = {
        firm: category
        for firm, category in category_by_firm.items()
        if firm not in previously_listed
    }
    if new_firms:
        logger.info(
            "supported companies: newly added: %s", ", ".join(sorted(new_firms))
        )
    return new_firms


def sync_and_announce(spreadsheet: gspread.Spreadsheet) -> None:
    """Sync supported firms, then announce new additions."""
    send_new_companies(
        sync_supported_companies(spreadsheet),
        spreadsheet_url=spreadsheet.url,
        total_firms=len(_category_by_firm()),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sync_and_announce(get_spreadsheet())
