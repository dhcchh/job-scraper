"""Supported-companies reference tab: every firm this project scrapes, laid
out wide -- one column per company_type, its companies listed down that
column -- kept in sync with the checked-in company_toml/*_boards.toml files
rather than maintained by hand.

Run standalone --

    uv run python -m jobscraper.sheets.supported_companies

-- which is also exactly what the "Announce New Companies" GitHub Actions
workflow (.github/workflows/announce-companies.yml) runs every day at 22:00
SGT, so a firm added to any of the six *_boards.toml files shows up here,
and is announced on Telegram, automatically at the next 22:00 with no
separate manual step (unlike sheets/backfill.py and sheets/full_refresh.py,
which are manual-only). main.run()'s daily scrape deliberately doesn't sync
this tab -- a firm is only "new" to whichever sync lists it first, so a
07:00 scrape doing it would announce there instead of at 22:00. This
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

Before clearing, sync_supported_companies reads which firms the tab already
listed, and returns the configured firms it didn't -- the firms added since
the last sync -- for telegram.send_new_companies to announce -- which this module's own
__main__ does, whether run by that workflow or by hand. A manual run
announces too, since whichever sync lists a firm first is the only one that
will ever see it as new.
"""

from __future__ import annotations

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
    """A company_type label as a lowercase snake_case column header, e.g.
    "Prop Trading/Hedge Fund" -> "prop_trading_hedge_fund", "AI Lab" ->
    "ai_lab". Today's company_type values don't collide once slugified --
    this doesn't guard against two different labels slugifying to the same
    string.
    """
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _category_by_firm() -> dict[str, str]:
    """firm -> company_type across all six *_boards.toml files.

    A firm with more than one board token (e.g. Optiver has two Greenhouse
    tokens) is one entry here, not one per token -- this tab answers "which
    firms are supported", not "which board tokens exist". The six
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
        + config.WORKDAY_BOARDS
    )
    category_by_firm: dict[str, str] = {}
    for board in all_boards:
        category_by_firm.setdefault(board["firm"], board["company_type"])
    return category_by_firm


def build_columns() -> dict[str, list[str]]:
    """slug -> sorted company names in that company_type, ordered by slug,
    one entry per firm (see _category_by_firm)."""
    firms_by_slug: dict[str, list[str]] = defaultdict(list)
    for firm, category in _category_by_firm().items():
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


def _listed_firms(worksheet: gspread.Worksheet) -> set[str]:
    """Every firm the tab lists right now: each non-blank cell below the
    header row, whichever column it's in."""
    rows = worksheet.get_all_values()
    return {cell.strip() for row in rows[1:] for cell in row if cell.strip()}


def sync_supported_companies(spreadsheet: gspread.Spreadsheet) -> dict[str, str]:
    """Rewrite the supported-companies tab from current config, from scratch.

    Cleared first, unlike sheets/writer.append_jobs' incremental append or
    sheets/backfill.py's in-place same-shape rewrite: this tab's shape
    tracks the current set of configured categories/firms directly, so a
    category or firm that disappears from every *_boards.toml file must
    disappear from the tab too, which an in-place update() over a shrinking
    range wouldn't do on its own.

    Returns the firms newly added since the tab was last written -- in
    config, not yet listed in the tab -- as firm -> company_type, for
    telegram.send_new_companies. The tab's own previous contents are the
    only record of "already announced" this needs, so there's no separate
    state to keep in sync. A tab that listed no firms at all (just created,
    or never populated) returns {} rather than every configured firm, so
    first-time setup doesn't announce the whole list as new. A renamed firm
    (see CLAUDE.md) does read as new under its new name.
    """
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
    """sync_supported_companies, then announce whatever it newly added.

    The announcement's firm count is recounted from config on every call
    rather than stored anywhere, so adding a firm to a *_boards.toml file
    is all it takes for the next announcement to say so.
    """
    send_new_companies(
        sync_supported_companies(spreadsheet),
        spreadsheet_url=spreadsheet.url,
        total_firms=len(_category_by_firm()),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Announced by any run of this, the scheduled workflow's or a manual
    # one: once this rewrites the tab, no later sync sees these firms as new.
    sync_and_announce(get_spreadsheet())
