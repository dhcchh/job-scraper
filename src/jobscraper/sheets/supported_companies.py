"""Supported-companies reference tab: every firm this project scrapes, laid
out wide -- one column per company_type, its companies listed down that
column -- kept in sync with the checked-in company_toml/*_boards.toml files
rather than maintained by hand.

Run standalone to (re)populate the tab immediately --

    uv run python -m jobscraper.sheets.supported_companies

-- and also called from main.run() at the end of every daily scrape, so a
firm added to any of the five *_boards.toml files shows up here
automatically on the very next run, with no separate manual step (unlike
sheets/backfill.py and sheets/full_refresh.py, which are manual-only). This
is safe to run unattended because it reasons about nothing but the current
config -- no in-flight job data, no existing-row state to merge with -- so
there's no interrupted-partway-through state it could ever leave stranded.

Column headers are a slugified form of company_type (e.g. "Prop
Trading/Hedge Fund" -> "prop_trading_hedge_fund"), not that string verbatim
-- self-explanatory as a spreadsheet header without carrying its mixed
case/slashes/ampersands along. Categories have different company counts, so
this is a ragged grid: shorter columns are padded with "" rather than left
absent, keeping every row the same length. sheets.writer.set_basic_filter is
deliberately NOT applied here, unlike every job tab -- a basic filter sorts
the whole range as one set of rows, which would scramble the alignment
between unrelated categories' columns since a row here isn't a record the
way a job posting's row is.

The tab itself is user-created (see CLAUDE.md) rather than one of the ten
(category, region) job tabs main.py already assumes exist, so
_get_or_create_worksheet matches TAB_NAME case/whitespace-insensitively
against the spreadsheet's existing tabs before falling back to creating one
-- a brand new spreadsheet (or a renamed tab) still gets a usable tab
instead of sheets.writer.get_worksheet's usual WorksheetNotFound.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

import gspread

from jobscraper import config
from jobscraper.sheets.writer import get_spreadsheet

logger = logging.getLogger(__name__)

TAB_NAME = "Supported companies"


def _slugify(label: str) -> str:
    """A company_type label as a lowercase snake_case column header, e.g.
    "Prop Trading/Hedge Fund" -> "prop_trading_hedge_fund", "AI Lab" ->
    "ai_lab". Today's company_type values don't collide once slugified --
    this doesn't guard against two different labels slugifying to the same
    string.
    """
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def build_columns() -> dict[str, list[str]]:
    """slug -> sorted company names in that company_type, ordered by slug.

    A firm with more than one board token (e.g. Optiver has two Greenhouse
    tokens) is one entry here, not one per token -- this tab answers "which
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

    firms_by_slug: dict[str, list[str]] = defaultdict(list)
    for firm, category in category_by_firm.items():
        firms_by_slug[_slugify(category)].append(firm)

    return {slug: sorted(firms) for slug, firms in sorted(firms_by_slug.items())}


def _grid(columns: dict[str, list[str]]) -> list[list[str]]:
    """The header row (one slug per category) plus every data row, each
    padded to the widest category's company count with "" so the grid is
    rectangular, as gspread's update() requires.
    """
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


def sync_supported_companies(spreadsheet: gspread.Spreadsheet) -> int:
    """Rewrite the supported-companies tab from current config, from scratch.

    Cleared first, unlike sheets/writer.append_jobs' incremental append or
    sheets/backfill.py's in-place same-shape rewrite: this tab's shape
    tracks the current set of configured categories/firms directly, so a
    category or firm that disappears from every *_boards.toml file must
    disappear from the tab too, which an in-place update() over a shrinking
    range wouldn't do on its own.

    Returns the total number of companies written across every column.
    """
    columns = build_columns()
    worksheet = _get_or_create_worksheet(spreadsheet, cols=len(columns))
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
    return total


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sync_supported_companies(get_spreadsheet())
