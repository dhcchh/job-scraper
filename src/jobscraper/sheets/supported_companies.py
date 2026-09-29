"""Sync configured firms to the supported-companies sheet tab."""

import logging
import re
from collections import defaultdict

import gspread

from jobscraper import config
from jobscraper.models import escape_sheet_text
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


def _grid(
    columns: dict[str, list[str]], min_rows: int = 0, min_cols: int = 0
) -> list[list[str]]:
    """Build the supported-companies header and data rows.

    Padded with "" to at least min_rows x min_cols, so writing it over a
    larger old grid blanks the cells it no longer uses.
    """
    width = max(len(columns), min_cols)
    height = max(max((len(firms) for firms in columns.values()), default=0), min_rows - 1)
    rows = [list(columns.keys()) + [""] * (width - len(columns))]
    for i in range(height):
        row = [firms[i] if i < len(firms) else "" for firms in columns.values()]
        rows.append(row + [""] * (width - len(row)))
    return rows


def _get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, cols: int) -> gspread.Worksheet:
    target = TAB_NAME.strip().casefold()
    for worksheet in spreadsheet.worksheets():
        if worksheet.title.strip().casefold() == target:
            return worksheet
    return spreadsheet.add_worksheet(title=TAB_NAME, rows=100, cols=max(cols, 1))


def _listed_columns(rows: list[list[str]]) -> dict[str, list[str]]:
    """Read the tab back as {header slug: [firms listed under it]}."""
    if not rows:
        return {}
    columns: dict[str, list[str]] = {}
    for i, header in enumerate(rows[0]):
        slug = header.strip()
        if not slug:
            continue
        firms = columns.setdefault(slug, [])
        for row in rows[1:]:
            firm = row[i].strip() if i < len(row) else ""
            if firm and firm not in firms:
                firms.append(firm)
    return columns


def _merge_columns(
    listed: dict[str, list[str]], configured: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Add configured firms to what the tab already lists, never dropping one.

    A configured firm sits under its configured category (moving it if the
    tab has it elsewhere); a firm no longer in config keeps its place.
    """
    configured_slug = {
        firm: slug for slug, firms in configured.items() for firm in firms
    }
    merged: dict[str, set[str]] = defaultdict(set)
    for slug, firms in listed.items():
        for firm in firms:
            merged[configured_slug.get(firm, slug)].add(firm)
    for firm, slug in configured_slug.items():
        merged[slug].add(firm)
    return {slug: sorted(firms) for slug, firms in sorted(merged.items())}


def sync_supported_companies(spreadsheet: gspread.Spreadsheet) -> dict[str, str]:
    """Add configured firms to the supported-companies tab without clearing it.

    Firms already listed are kept even if they're no longer configured, so a
    run with a partial config can never wipe the tab. Returns the configured
    firms the tab didn't list before (firm -> company_type).
    """
    category_by_firm = _category_by_firm()
    configured = build_columns()
    worksheet = _get_or_create_worksheet(spreadsheet, cols=len(configured))
    old_rows = worksheet.get_all_values()
    listed = _listed_columns(old_rows)
    previously_listed = {firm for firms in listed.values() for firm in firms}
    columns = _merge_columns(listed, configured)
    if columns and columns != listed:
        grid = _grid(
            columns,
            min_rows=len(old_rows),
            min_cols=max((len(row) for row in old_rows), default=0),
        )
        worksheet.update(
            range_name="A1",
            values=[[escape_sheet_text(cell) for cell in row] for row in grid],
            value_input_option="USER_ENTERED",
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
