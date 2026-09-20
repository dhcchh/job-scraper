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

Nine things happen to every row, for every (category, region) tab:
  - `seniority`, `company_type`, and `source` are recomputed from columns
    already in the row -- `seniority` from the row's own
    `title`/`department` via the *current* classify_seniority.
    classify_seniority; `company_type` and `source` by looking the row's
    `board` up in the *current* config.GREENHOUSE_BOARDS,
    config.LEVER_BOARDS, config.ASHBY_BOARDS,
    config.SMARTRECRUITERS_BOARDS, and config.EIGHTFOLD_BOARDS (whichever
    one the firm is actually configured in -- `source` is just which of the
    five that is,
    "Greenhouse"/"Lever"/"Ashby"/"SmartRecruiters"/"Eightfold"). Fixing
    classify_seniority.py's "misses numeric/leveling titles" gap,
    re-categorizing a firm in any of the five `*_boards.toml` files, or
    moving a firm from one board system to another, and then running this
    backfill applies the change to every existing row, not just new ones.
  - `location` is corrected using a *fresh* re-fetch of every board in
    config.GREENHOUSE_BOARDS, config.LEVER_BOARDS, config.ASHBY_BOARDS,
    config.SMARTRECRUITERS_BOARDS, and config.EIGHTFOLD_BOARDS (see
    _fetch_overrides) when the row's posting is still open -- that
    re-fetch has real per-office data, so it's what actually fixes a
    legacy, never-split cell like "Chicago, New York City" (written before
    this project read per-office data at all -- see
    job_boards/greenhouse/client.py). Only once a posting has actually
    closed (gone from the API, no override available) does this fall back
    to _renormalize_location's best-effort re-normalization of whatever
    string is already in the cell.
  - `first_published` is filled in from that same fresh re-fetch, for a row
    still open -- a genuinely new column added after most rows already
    existed, so there's no legacy cell to re-derive a value from, and a
    closed posting's row is left exactly as it already is (empty, if it
    predates the column) rather than guessed at.
  - `salary` is recomputed unconditionally from that same fresh re-fetch,
    for a row still open -- like `first_published`, there's no legacy cell
    to re-derive it from, but like `location`, a fresh value (including null
    if compensation disclosure was removed) always overwrites whatever's
    there rather than only filling in a blank; see models.Job.salary and
    job_boards/lever/client.py's and job_boards/ashby/client.py's own
    `_salary` for where this actually comes from. A closed posting's row,
    with no override available, is left exactly as it already is.
  - `description` is recomputed unconditionally from
    models.FIRMS_NEEDING_DESCRIPTION, the same "always trust current code,
    not the existing cell" treatment as `company_type`/`source` above, not
    the "only fill in, never blank" treatment `first_published` gets: a row
    whose `board` is in that set gets the fresh re-fetch's `description`
    (which overwrites even a non-empty existing value when the text
    differs -- a JD can be edited after this project first saw it, and
    there's no signal short of the text itself changing to tell a genuine
    edit apart from staleness); a row whose `board` is NOT in that set has
    `description` blanked back to "" if it has anything there at all. This
    is deliberate, not the usual "never guess-blank a field" caution --
    narrowing FIRMS_NEEDING_DESCRIPTION is expected to un-fill rows for a
    firm that's been removed from it, and this is the one place that
    actually happens.
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
  - `url` is re-wrapped in models.hyperlink_formula, whether the cell is
    still a legacy raw url (written before that existed) or already one of
    these formulas -- get_all_values()'s ordinary FORMATTED read returns an
    already-wrapped cell's *label*, not its url, so recovering the real url
    to re-wrap needs a second, FORMULA-rendered read of just this column
    (see _url_formula_cells/_extract_url). Without this, a backfill run
    would silently replace every url with the literal text "Apply".
  - the whole tab is rewritten with value_input_option="USER_ENTERED" (same
    as sheets.writer's own two functions), so a date written back when this
    repo still used "RAW" gets re-parsed into a real Sheets date too.

Once per tab, after every row above is rewritten, sheets.writer's
format_date_columns, format_description_column, and set_basic_filter are
re-applied against whatever column letters FIELDS' *current* order puts
first_published/updated_at/scraped_date/description and the header row at
-- so reordering FIELDS moves all three for free on the next backfill. The
one thing this can't reach is each tab's own conditional-formatting rule
(see CLAUDE.md): that lives entirely in Sheets' UI-managed formatting, not
the value grid this script rewrites, so its hardcoded cell reference has
to be repointed by hand after a reorder.

Deliberately NOT done here: re-routing a job to a different tab if
classify_roles.classify_job's category keywords, or classify_region's PAC
country list, change. Category and region are which tab a row lives in,
not a column inside one -- correcting either would mean moving the row
into another tab's own dedup key space, which risks duplicating or losing
rows if this script is interrupted partway through. Out of scope until
that's actually needed.

refresh_all() does re-fetch Greenhouse, Lever, Ashby, SmartRecruiters, and
Eightfold (for location/first_published/description overrides -- see
above), but only ever *reads* them: a closed posting is gone from its
board's API forever, so for every OTHER column, a row's own text (title,
department, url, ...) is the only surviving source of truth for itself --
everything except `location`/`first_published`/`description` only ever
recomputes from columns already in the row.

A firm dropped from all five `*_boards.toml` files entirely has no entry
to look company_type or source up against -- its existing rows are left
with whatever company_type/source they already have rather than being
blanked out.
"""

from __future__ import annotations

import logging
import re

import gspread
from gspread.utils import rowcol_to_a1

from jobscraper import config
from jobscraper.enrichment.classify_region import REGIONS, tab_name
from jobscraper.enrichment.classify_roles import CATEGORIES
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location
from jobscraper.job_boards.ashby.client import fetch_ashby_jobs
from jobscraper.job_boards.eightfold.client import fetch_eightfold_jobs
from jobscraper.job_boards.greenhouse.client import fetch_greenhouse_jobs
from jobscraper.job_boards.lever.client import fetch_lever_jobs
from jobscraper.job_boards.smartrecruiters.client import fetch_smartrecruiters_jobs
from jobscraper.models import (
    FIELDS,
    FIRMS_NEEDING_DESCRIPTION,
    Job,
    hyperlink_formula,
    normalize_date,
)
from jobscraper.sheets.writer import (
    HEADER,
    format_date_columns,
    format_description_column,
    get_spreadsheet,
    get_worksheet,
    set_basic_filter,
)

logger = logging.getLogger(__name__)

# Matches exactly what models.hyperlink_formula produces, to unwrap it back
# to the real url -- see _extract_url.
_HYPERLINK_RE = re.compile(
    r'^=HYPERLINK\(\s*"((?:[^"]|"")*)"\s*,\s*"(?:[^"]|"")*"\s*\)$', re.IGNORECASE
)


def _extract_url(cell: str) -> str:
    """The real url behind a url-column cell.

    A cell already wrapped by models.hyperlink_formula reads back through
    get_all_values() as its *label* (e.g. "Apply"), not the url it points
    to -- Sheets renders a formula, it doesn't echo its source. Only a
    FORMULA-rendered read (see _url_formula_cells) recovers the original
    `=HYPERLINK("url", "label")` text this can unwrap. A cell that isn't
    that formula at all -- a legacy plain url, or "" -- is returned
    unchanged, since that legacy shape *is* already the real url.
    """
    match = _HYPERLINK_RE.match(cell.strip())
    if not match:
        return cell
    return match.group(1).replace('""', '"')


def _url_formula_cells(
    worksheet: gspread.Worksheet, header: list[str], num_rows: int
) -> list[str]:
    """Formula text of the url column, one entry per data row.

    Fetched with value_render_option="FORMULA" specifically for this one
    column -- the ordinary FORMATTED read `all_values` uses for every other
    column would, for an already-migrated cell, return the label
    hyperlink_formula wrapped the url in rather than the url itself,
    permanently losing it on the very next backfill (see _extract_url).
    Every other column backfill recomputes has no such formula to lose, so
    only url needs this second, narrower read. Returns num_rows empty
    strings if this tab's header has no url column at all (shouldn't
    happen -- url is a core field -- but matches how every other column
    here degrades rather than raising).
    """
    if "url" not in header or num_rows == 0:
        return [""] * num_rows
    col = header.index("url") + 1
    cell_range = f"{rowcol_to_a1(2, col)}:{rowcol_to_a1(num_rows + 1, col)}"
    rows = worksheet.get(
        cell_range,
        value_render_option="FORMULA",
        pad_values=True,
        maintain_size=True,
    )
    return [row[0] if row else "" for row in rows]


def _tab_rows_for_refresh(
    worksheet: gspread.Worksheet,
) -> tuple[list[list[str]], dict[str, int | None], list[str]] | None:
    """This tab's data rows (header excluded), a FIELDS name -> current
    column index map, and each data row's url-column formula text -- or
    None if there are no data rows to refresh (an empty tab, or header
    only).

    Shared by refresh_worksheet and sheets.full_refresh._read_tab_rows --
    the same "read this tab and get it ready for a per-row
    _recompute_row_values call" preamble both need. The index map lets a
    field still be located regardless of the column order (or column set)
    this particular tab's header currently has -- a name FIELDS has that
    the header doesn't maps to None, not a KeyError; _read_row_values and
    _recompute_row_values both treat that exactly like a too-short row.
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        return None
    header, *data_rows = all_values
    if not data_rows:
        return None
    old_index = {
        name: (header.index(name) if name in header else None) for name in FIELDS
    }
    url_formula_cells = _url_formula_cells(worksheet, header, len(data_rows))
    return data_rows, old_index, url_formula_cells


def _fetch_overrides() -> dict[tuple[str, str], Job]:
    """Every currently open posting, freshly re-fetched, keyed by (board,
    job_id) -- one re-fetch of every board in config.GREENHOUSE_BOARDS,
    config.LEVER_BOARDS, config.ASHBY_BOARDS,
    config.SMARTRECRUITERS_BOARDS, and config.EIGHTFOLD_BOARDS (the same
    five loops main.py's daily run makes) serves four different
    retroactive fixes without doubling the network cost:

    - `location`: the only way to correctly fix a legacy multi-location cell
      that was never split (written before a board's client read its
      per-office data at all) -- there's no way to safely re-split e.g.
      "Chicago, New York City" from the string alone, but that board's own
      per-office data for that same still-open posting has it pre-split,
      same as any fresh fetch.
    - `first_published`: a column that may not exist at all yet in an
      already-written tab (added after FIELDS already had rows in it) --
      there is no cell to re-derive it from, so a fresh fetch is the only
      source of truth.
    - `description`, for a firm in models.FIRMS_NEEDING_DESCRIPTION only --
      same "no cell to re-derive from" reasoning as first_published, but
      unlike it, this override also replaces a stale-but-present value
      (a JD can be legitimately edited after this project first saw it).
      A firm NOT in that set gets its description blanked back to ""
      unconditionally in refresh_worksheet, whether or not an override is
      available here -- that decision doesn't depend on this re-fetch at
      all.
    - `salary` (models.Job.salary) -- same "no cell to re-derive from"
      reasoning again, and like `description` (but unlike
      `first_published`), this override replaces a stale-but-present value
      too, including with null if compensation is no longer disclosed. Unlike
      `description`, there's no firm allowlist gating this one at all --
      see models.Job.salary's own docstring for why.

    A posting that's since closed has no entry here for any of the above --
    refresh_worksheet falls back to best-effort re-normalization for
    location, and leaves first_published/salary exactly as they already are
    (empty, for a row written before those columns existed, since there's
    nothing to recover them from once a posting is gone).

    A board that fails to fetch (network error, bad token, ...) is logged
    and skipped -- its rows just get no override for any of these columns
    this run. Merging all five board types into one dict is safe because
    the dedup key is (board, job_id) where `board` is the firm's name, not
    the board type -- a firm is only ever configured in one of the five
    files.
    """
    overrides: dict[tuple[str, str], Job] = {}
    for board_list, fetch in (
        (config.GREENHOUSE_BOARDS, fetch_greenhouse_jobs),
        (config.LEVER_BOARDS, fetch_lever_jobs),
        (config.ASHBY_BOARDS, fetch_ashby_jobs),
        (config.SMARTRECRUITERS_BOARDS, fetch_smartrecruiters_jobs),
        (config.EIGHTFOLD_BOARDS, fetch_eightfold_jobs),
    ):
        for board in board_list:
            firm = board["firm"]
            try:
                jobs = fetch(board["token"], firm, board["company_type"])
            except Exception:
                logger.warning(
                    "refresh: couldn't re-fetch %s for location/first_published"
                    "/description overrides, its rows keep whatever they "
                    "already have",
                    firm,
                )
                continue
            for job in jobs:
                overrides[(job.board, job.job_id)] = job
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
    reopens through that board's fetch_<board>_jobs, which does have
    offices to work from.
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


def _config_lookup_tables() -> tuple[dict[str, str], dict[str, str]]:
    """(company_type_by_firm, source_by_firm), from the current
    *_boards.toml files -- shared by refresh_worksheet and
    sheets.full_refresh so both look a row's `board` up against identical,
    up-to-date config rather than each keeping its own copy.
    """
    board_lists_with_source = (
        (config.GREENHOUSE_BOARDS, "Greenhouse"),
        (config.LEVER_BOARDS, "Lever"),
        (config.ASHBY_BOARDS, "Ashby"),
        (config.SMARTRECRUITERS_BOARDS, "SmartRecruiters"),
        (config.EIGHTFOLD_BOARDS, "Eightfold"),
    )
    company_type_by_firm = {
        board["firm"]: board["company_type"]
        for board_list, _source in board_lists_with_source
        for board in board_list
    }
    source_by_firm = {
        board["firm"]: source
        for board_list, source in board_lists_with_source
        for board in board_list
    }
    return company_type_by_firm, source_by_firm


def _read_row_values(row: list[str], old_index: dict[str, int | None]) -> dict[str, str]:
    """This row's current FIELDS values, as-is off the sheet -- a name FIELDS
    has that the tab's header doesn't (old_index[name] is None), or a row
    shorter than that index, reads as "" rather than raising.

    Shared by _recompute_row_values (as its starting point before
    recomputing any column) and refresh_worksheet (as the "before" snapshot
    it diffs the recomputed result against for its changed-column counts) --
    the same raw read, wanted for two different purposes.
    """
    def read(name: str) -> str:
        i = old_index[name]
        if i is None or i >= len(row):
            return ""
        return row[i]

    return {name: read(name) for name in FIELDS}


def _recompute_row_values(
    row: list[str],
    url_cell: str,
    old_index: dict[str, int | None],
    company_type_by_firm: dict[str, str],
    source_by_firm: dict[str, str],
    overrides: dict[tuple[str, str], Job] | None,
) -> dict[str, str]:
    """One row's FIELDS, recomputed from current classifiers/config/overrides
    -- every column EXCEPT which tab the row belongs on.

    Shared by refresh_worksheet (rewrites a row in place, in its current
    tab) and sheets.full_refresh (which also re-routes a row to a different
    tab when its category or region has changed) so both apply identical
    column logic to a row; only what happens to a row whose classification
    changed differs between the two callers. See refresh_worksheet's own
    docstring for what each field does and why.

    `salary` follows the same "only a fresh re-fetch can supply it, nothing
    to re-derive from an existing cell" reasoning as `first_published` and
    `description`'s overrides -- but unlike `description`, it isn't gated to
    FIRMS_NEEDING_DESCRIPTION (see models.Job.salary), so a fresh value
    (including null, if a posting's compensation disclosure was removed, or
    the board doesn't support it) always wins when available, the same
    unconditional-trust-the-fresh-fetch treatment `location` gets. A row
    with no override at all (posting closed, or its board's fetch failed)
    keeps whatever it already has.
    """

    values = _read_row_values(row, old_index)

    values["seniority"] = classify_seniority(values["title"], values["department"])

    new_company_type = company_type_by_firm.get(values["board"])
    if new_company_type is not None:
        values["company_type"] = new_company_type

    new_source = source_by_firm.get(values["board"])
    if new_source is not None:
        values["source"] = new_source

    fresh = overrides.get((values["board"], values["job_id"])) if overrides else None

    values["location"] = (
        fresh.location if fresh is not None else _renormalize_location(values["location"])
    )

    if fresh is not None and fresh.first_published:
        fresh_fp = normalize_date(fresh.first_published)
        if fresh_fp:
            values["first_published"] = fresh_fp

    if values["board"] not in FIRMS_NEEDING_DESCRIPTION:
        values["description"] = ""
    elif fresh is not None and fresh.description:
        values["description"] = fresh.description

    if fresh is not None:
        # Sheets has no null cell value. Retain null in Job itself, but write
        # an empty cell so a prior salary is explicitly cleared on refresh.
        values["salary"] = fresh.salary or ""

    values["first_published"] = normalize_date(values["first_published"])
    values["updated_at"] = normalize_date(values["updated_at"])
    values["scraped_date"] = normalize_date(values["scraped_date"])
    values["url"] = hyperlink_formula(_extract_url(url_cell))

    return values


def refresh_worksheet(
    worksheet: gspread.Worksheet,
    *,
    overrides: dict[tuple[str, str], Job] | None = None,
) -> None:
    """Recompute derived columns, re-lay-out, and rewrite every row in this tab.

    `overrides` -- (board, job_id) -> a freshly re-fetched Job, from
    _fetch_overrides() -- takes priority over _renormalize_location's
    best-effort cleanup for `location`, and is the *only* source for
    `first_published`, `salary`, and for filling in `description` on a row
    whose firm is in models.FIRMS_NEEDING_DESCRIPTION (nothing to re-derive
    any of those from an existing cell). Omit it (the default) to fall back
    to best-effort location cleanup and leave first_published/salary
    untouched for every row, e.g. when re-fetching boards isn't wanted for
    a given run -- `description` still gets blanked for every OTHER firm
    regardless, since that decision only depends on FIRMS_NEEDING_DESCRIPTION,
    not `overrides`.

    Always rewrites the managed A:M range as long as there's at least one
    data row -- unlike prune_closed_postings, this never skips based on
    whether a value *looks* unchanged, since e.g. a plain-text date and a
    real Sheets date read back identically from get_all_values()'s string
    output; only re-submitting with USER_ENTERED (and, for column order,
    unconditionally re-laying the row out) actually fixes one written under
    older logic. It never clears the worksheet first, so a failed update
    leaves the previous values intact and columns outside the managed range
    remain untouched.
    """
    loaded = _tab_rows_for_refresh(worksheet)
    if loaded is None:
        logger.info("refresh: no data rows, nothing to do")
        return
    data_rows, old_index, url_formula_cells = loaded
    company_type_by_firm, source_by_firm = _config_lookup_tables()

    new_rows = []
    seniority_changed = 0
    company_type_changed = 0
    source_changed = 0
    location_changed = 0
    first_published_changed = 0
    description_changed = 0
    salary_changed = 0
    for row, url_cell in zip(data_rows, url_formula_cells):
        old_values = _read_row_values(row, old_index)
        values = _recompute_row_values(
            row, url_cell, old_index, company_type_by_firm, source_by_firm, overrides
        )

        if old_values["seniority"] != values["seniority"]:
            seniority_changed += 1
        if old_values["company_type"] != values["company_type"]:
            company_type_changed += 1
        if old_values["source"] != values["source"]:
            source_changed += 1
        if old_values["location"] != values["location"]:
            location_changed += 1
        if old_values["first_published"] != values["first_published"]:
            first_published_changed += 1
        if old_values["description"] != values["description"]:
            description_changed += 1
        if old_values["salary"] != values["salary"]:
            salary_changed += 1

        new_rows.append([values[name] for name in FIELDS])

    worksheet.update(
        range_name="A1",
        values=[HEADER] + new_rows,
        value_input_option="USER_ENTERED",
    )
    format_date_columns(worksheet)
    format_description_column(worksheet)
    set_basic_filter(worksheet)
    logger.info(
        "refresh: rewrote %d row(s) into current column order -- "
        "%d seniority, %d company_type, %d source, %d location, "
        "%d first_published, %d description, %d salary changed",
        len(new_rows),
        seniority_changed,
        company_type_changed,
        source_changed,
        location_changed,
        first_published_changed,
        description_changed,
        salary_changed,
    )


def refresh_all() -> None:
    """Refresh every (category, region) tab in the spreadsheet, one at a time."""
    overrides = _fetch_overrides()
    spreadsheet = get_spreadsheet()
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            logger.info("refresh: %s", tab)
            worksheet = get_worksheet(spreadsheet, tab)
            refresh_worksheet(worksheet, overrides=overrides)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    refresh_all()
