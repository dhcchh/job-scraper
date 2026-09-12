"""Full-refresh backfill: re-derive and re-lay-out every row already in a tab.

Run this by hand --

    uv run python -m jobscraper.sheets.backfill

-- after a change to how a row is classified, written, or *ordered*, so it
applies retroactively to rows that were already there, not just to postings
fetched from here on. main.py's daily run never does this on its own:
append_jobs only ever touches brand-new rows, and prune_closed_postings only
rewrites a tab when it actually has something to prune, preserving that
tab's existing header and column order exactly as found (see its docstring)
-- a tab with no closures on a given day is left completely untouched, so
an already-written row can sit on stale classification/formatting/column
order indefinitely without this.

Four things happen to every row, for every (category, region) tab:
  - `seniority` and `company_type` are recomputed from columns already in
    the row -- `seniority` from the row's own `title`/`department` via the
    *current* classify_seniority.classify_seniority, `company_type` by
    looking the row's `board` up in the *current*
    config.GREENHOUSE_BOARDS. Fixing classify_seniority.py's "misses
    numeric/leveling titles" gap, or re-categorizing a firm in
    greenhouse_boards.toml, and then running this backfill applies the
    change to every existing row, not just new ones.
  - `location` is corrected using a *fresh* re-fetch of every board in
    config.GREENHOUSE_BOARDS (see _location_overrides_from_greenhouse) when
    the row's posting is still open -- that re-fetch has real `offices`
    data, so it's what actually fixes a legacy, never-split cell like
    "Chicago, New York City" (written before this project read `offices`
    at all -- see job_boards/greenhouse/client.py). Only once a posting has
    actually closed (gone from the API, no override available) does this
    fall back to _renormalize_location's best-effort re-normalization of
    whatever string is already in the cell.
  - every row is re-laid-out into the *current* jobscraper.models.FIELDS
    column order, and the header row is overwritten with that same order
    (sheets.writer.HEADER) rather than left as whatever the tab already
    had. This is what makes reordering FIELDS (e.g. to put the columns
    people actually filter by first) a real migration instead of a
    same-run misalignment: after this runs once, main.py's append_jobs --
    which writes Job.to_row() in FIELDS order -- lines up with the tab's
    header again. A cell missing because a row was shorter than the header
    (a normal, common shape -- Sheets/gspread trims trailing blank cells)
    is read back as "", not an error.
  - the whole tab is rewritten with value_input_option="USER_ENTERED" (same
    as sheets.writer's own two functions), so a date/url written back when
    this repo still used "RAW" gets re-parsed into a real Sheets date / an
    auto-linked url too.

Deliberately NOT done here: re-routing a job to a different tab if
classify_roles.classify_job's category keywords, or classify_region's PAC
country list, change. Category and region are which tab a row lives in,
not a column inside one -- correcting either would mean moving the row
into another tab's own dedup key space, which risks duplicating or losing
rows if this script is interrupted partway through. Out of scope until
that's actually needed.

refresh_all() does re-fetch Greenhouse (for location overrides -- see
above), but only ever *reads* it: a closed posting is gone from the API
forever, so for every OTHER column, a row's own text (title, department,
url, ...) is the only surviving source of truth for itself -- everything
except `location` only ever recomputes from columns already in the row.

A firm dropped from greenhouse_boards.toml entirely has no entry to look
company_type up against -- its existing rows are left with whatever
company_type they already have rather than being blanked out.
"""

from __future__ import annotations

import logging

import gspread

from jobscraper import config
from jobscraper.classifications.classify_region import REGIONS, tab_name
from jobscraper.classifications.classify_roles import CATEGORIES
from jobscraper.classifications.classify_seniority import classify_seniority
from jobscraper.classifications.normalize_location import normalize_location
from jobscraper.job_boards.greenhouse.client import fetch_jobs
from jobscraper.models import FIELDS
from jobscraper.sheets.writer import HEADER, _column_index, get_spreadsheet, get_worksheet

logger = logging.getLogger(__name__)


def _location_overrides_from_greenhouse() -> dict[tuple[str, str], str]:
    """Fresh, offices-derived Job.location for every currently open posting,
    keyed by (board, job_id) -- re-fetches every board in
    config.GREENHOUSE_BOARDS the same way main.py's daily run does. This is
    the only way to correctly fix a legacy multi-location cell that was
    never split (written before job_boards/greenhouse/client.py read
    `offices`): there's no way to safely re-split e.g. "Chicago, New York
    City" from the string alone, but Greenhouse's own `offices` array for
    that same still-open posting has it pre-split, same as any fresh fetch.

    A board that fails to fetch (network error, bad token, ...) is logged
    and skipped -- its rows just fall back to refresh_worksheet's
    best-effort re-normalization, same as if this function didn't exist.
    """
    overrides: dict[tuple[str, str], str] = {}
    for board in config.GREENHOUSE_BOARDS:
        firm = board["firm"]
        try:
            jobs = fetch_jobs(board["token"], firm, board["company_type"])
        except Exception:
            logger.warning(
                "refresh: couldn't re-fetch %s for location overrides, its "
                "rows will fall back to best-effort re-normalization",
                firm,
            )
            continue
        for job in jobs:
            overrides[(job.board, job.job_id)] = job.location
    return overrides


def _renormalize_location(value: str) -> str:
    """Best-effort retroactive location cleanup for an already-written cell.

    Splits on " | " (today's join delimiter, see client.py's _location) and
    re-normalizes each piece -- a cell already split by a previous run of
    this script picks up any change to normalize_location.py's table for
    free. A cell written before location normalization existed at all is
    one un-split piece (e.g. a recruiter's raw "Jersey, Montreal, Warsaw"),
    so it's normalized as a single string -- fixes it if it was really one
    plain city name, leaves an old multi-location string alone otherwise
    (normalize_location won't match it against any single-city table entry,
    so it falls through unchanged, same as any other unmapped name). There's
    no offices data left in the sheet to re-split a legacy multi-location
    string by -- it self-heals the next time that posting closes and
    reopens through fetch_jobs, which does have offices to work from.
    """
    pieces = [p.strip() for p in value.split(" | ") if p.strip()]
    if not pieces:
        return value

    normalized: list[str] = []
    for piece in pieces:
        name = normalize_location(piece)
        if name and name not in normalized:
            normalized.append(name)
    return " | ".join(normalized)


def refresh_worksheet(
    worksheet: gspread.Worksheet,
    *,
    location_overrides: dict[tuple[str, str], str] | None = None,
) -> None:
    """Recompute derived columns, re-lay-out, and rewrite every row in this tab.

    `location_overrides` -- (board, job_id) -> fresh Job.location, from
    _location_overrides_from_greenhouse() -- takes priority over
    _renormalize_location's best-effort cleanup for any row it has an entry
    for. Omit it (the default) to fall back to best-effort for every row,
    e.g. when re-fetching Greenhouse isn't wanted for a given run.

    Always rewrites (clear() + update()) as long as there's at least one
    data row -- unlike prune_closed_postings, this never skips based on
    whether a value *looks* unchanged, since e.g. a plain-text date and a
    real Sheets date read back identically from get_all_values()'s string
    output; only re-submitting with USER_ENTERED (and, for column order,
    unconditionally re-laying the row out) actually fixes one written under
    older logic.
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        logger.info("refresh: empty sheet, nothing to do")
        return

    header, *data_rows = all_values
    if not data_rows:
        logger.info("refresh: header only, nothing to do")
        return

    # Index into the tab's CURRENT header -- which may predate a FIELDS
    # reorder -- so every field can still be located regardless of the
    # column order this particular tab happens to be in right now.
    old_index = {name: _column_index(header, name) for name in FIELDS}
    company_type_by_firm = {
        board["firm"]: board["company_type"] for board in config.GREENHOUSE_BOARDS
    }

    def read(row: list[str], name: str) -> str:
        i = old_index[name]
        return row[i] if i < len(row) else ""

    new_rows = []
    seniority_changed = 0
    company_type_changed = 0
    location_changed = 0
    for row in data_rows:
        values = {name: read(row, name) for name in FIELDS}

        new_seniority = classify_seniority(values["title"], values["department"])
        if values["seniority"] != new_seniority:
            seniority_changed += 1
            values["seniority"] = new_seniority

        new_company_type = company_type_by_firm.get(values["board"])
        if new_company_type is not None and values["company_type"] != new_company_type:
            company_type_changed += 1
            values["company_type"] = new_company_type

        override = (
            location_overrides.get((values["board"], values["job_id"]))
            if location_overrides
            else None
        )
        new_location = (
            override if override is not None else _renormalize_location(values["location"])
        )
        if values["location"] != new_location:
            location_changed += 1
            values["location"] = new_location

        new_rows.append([values[name] for name in FIELDS])

    worksheet.clear()
    worksheet.update(values=[HEADER] + new_rows, value_input_option="USER_ENTERED")
    logger.info(
        "refresh: rewrote %d row(s) into current column order -- "
        "%d seniority, %d company_type, %d location changed",
        len(new_rows),
        seniority_changed,
        company_type_changed,
        location_changed,
    )


def refresh_all() -> None:
    """Refresh every (category, region) tab in the spreadsheet, one at a time."""
    location_overrides = _location_overrides_from_greenhouse()
    spreadsheet = get_spreadsheet()
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            logger.info("refresh: %s", tab)
            worksheet = get_worksheet(spreadsheet, tab)
            refresh_worksheet(worksheet, location_overrides=location_overrides)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    refresh_all()
