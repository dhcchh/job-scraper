"""Full reclassification refresh: recompute every column, and move a row to
a different (category, region) tab if its classification has changed.

Run this by hand --

    uv run python -m jobscraper.sheets.full_refresh

-- after a change to classify_roles.classify_job's category keywords or
classify_region.classify_region's PAC country list (e.g. Thailand moving
into PAC and Vietnam moving out -- see CLAUDE.md's classify_region bullet),
so an existing row whose category or region no longer matches the tab it's
sitting in actually gets moved there, rather than staying misclassified
until that specific posting happens to close and reopen.

sheets.backfill.refresh_worksheet deliberately never does this -- see its
module docstring's "Deliberately NOT done here" paragraph -- because moving
a row to a different tab risks duplicating or losing it if a per-tab loop
is interrupted partway through: by the time tab N's rows are being removed,
tab N's own read is long gone, so a crash before tab M (the row's
destination) is written loses the row outright.

This script avoids that specific failure mode by reading and reclassifying
every row across all ten tabs FIRST, entirely in memory, and only starting
to write once every row's destination tab is already decided -- so a
row's presence in memory never depends on a write that hasn't happened yet.
It is NOT a single atomic transaction across the ten separate Sheets API
calls that follow (this project has no distributed lock -- see CLAUDE.md's
Open Work section), so an interruption partway through the write loop can
still leave some tabs updated and others not; the guarantee is only that no
row is ever dropped or duplicated because of the order tabs happen to be
processed in, not that the whole run is all-or-nothing.

Every column other than which tab a row lives on is recomputed exactly the
way sheets.backfill.refresh_worksheet already does (see its docstring) --
this module reuses that same per-row logic via
sheets.backfill._recompute_row_values rather than duplicating it.

A row whose recomputed title no longer matches any classify_roles.CATEGORIES
keyword list (classify_job returns None -- e.g. a keyword was narrowed) has
nowhere else to go. Rather than silently dropping a real posting from the
tracker, it stays on the tab it's already in. This is a conscious,
conservative default -- matching this project's general "never guess-blank
a field" caution (see CLAUDE.md) applied to whole rows instead of a single
column -- not an oversight; if that's ever wrong for a specific posting, fix
classify_job's keywords and rerun, don't special-case it here.

A row whose (refreshed) location is wholly in a classify_region.is_excluded
country -- unlike the no-category case above -- is genuinely removed from
the sheet entirely, not left in place: this project has decided outright
not to carry those postings (see CLAUDE.md's classify_region bullet), so an
existing row that predates that decision, or whose location was corrected
by this same refresh into now being wholly excluded, shouldn't survive it
either. A multi-location row pairing an excluded country with a real PAC or
RoW office is unaffected -- is_excluded only fires when EVERY office is
excluded -- and gets classified (and possibly moved) normally instead.

A (board, job_id) key seen more than once across the ten tabs (shouldn't
happen in normal operation -- sheets.writer.append_jobs dedupes within a
tab at write time, and a job is only ever classified into exactly one tab
in the first place -- but could in principle follow an interrupted run)
keeps its first occurrence and logs a warning for the rest, rather than
writing the same posting into two tabs.
"""

from __future__ import annotations

import datetime
import logging

import gspread

from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    is_excluded,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.models import FIELDS, Job
from jobscraper.sheets.backfill import (
    _config_lookup_tables,
    _fetch_overrides,
    _recompute_row_values,
    _url_formula_cells,
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


def _classify(values: dict[str, str]) -> str | None:
    """The category `values` (a recomputed row) now belongs to, or None.

    classify_job takes a Job and reads only its `.title` -- see
    classify_roles.py's module docstring on why department is deliberately
    excluded from that matching. Building a real (if mostly-unused-field)
    Job from the row's own recomputed values, rather than re-implementing
    classify_job's title-matching rules here, keeps that logic defined in
    exactly one place (see CLAUDE.md's classify_region guidance: fix
    classification in the classifier, not by special-casing a caller).
    """
    stub = Job(
        board=values["board"],
        job_id=values["job_id"],
        title=values["title"],
        department=values["department"],
        location=values["location"],
        url="",
        updated_at="",
        scraped_date=datetime.date.today(),
    )
    return classify_job(stub)


def _read_tab_rows(
    worksheet: gspread.Worksheet,
    company_type_by_firm: dict[str, str],
    source_by_firm: dict[str, str],
    overrides: dict[tuple[str, str], Job],
    origin_category: str,
    origin_region: str,
) -> tuple[list[tuple[str, dict[str, str]]], int]:
    """Every KEPT row in this tab, recomputed, each paired with its
    destination tab name, plus how many rows were dropped outright (see
    module docstring's is_excluded paragraph).

    origin_category/origin_region are this row's CURRENT tab -- where a row
    stays if classify_job now returns None for it (see module docstring).
    """
    all_values = worksheet.get_all_values()
    if not all_values:
        return [], 0
    header, *data_rows = all_values
    if not data_rows:
        return [], 0

    old_index = {
        name: (header.index(name) if name in header else None) for name in FIELDS
    }
    url_formula_cells = _url_formula_cells(worksheet, header, len(data_rows))

    origin_tab = tab_name(origin_category, origin_region)
    rows: list[tuple[str, dict[str, str]]] = []
    dropped = 0
    for row, url_cell in zip(data_rows, url_formula_cells):
        values = _recompute_row_values(
            row, url_cell, old_index, company_type_by_firm, source_by_firm, overrides
        )
        category = _classify(values)
        if category is None:
            logger.warning(
                "full_refresh: %r (board=%s, job_id=%s) no longer matches any "
                "category -- leaving it on %s instead of dropping it",
                values["title"],
                values["board"],
                values["job_id"],
                origin_tab,
            )
            dest = origin_tab
        elif is_excluded(values["location"]):
            logger.warning(
                "full_refresh: dropping %r (board=%s, job_id=%s) -- every "
                "office in %r is an excluded country",
                values["title"],
                values["board"],
                values["job_id"],
                values["location"],
            )
            dropped += 1
            continue
        else:
            dest = tab_name(category, classify_region(values["location"]))
        rows.append((dest, values))
    return rows, dropped


def _write_tab(worksheet: gspread.Worksheet, rows: list[list[str]]) -> None:
    """Rewrite this tab's entire data range with exactly `rows`.

    Unlike backfill.refresh_worksheet (which always rewrites the same
    number of rows a tab already has), a full refresh can shrink a tab to
    fewer rows than it currently holds -- every row having moved to another
    tab, say -- so the grid is explicitly resized to `len(rows) + 1` first;
    otherwise a plain update() would leave stale rows sitting below
    whatever it actually wrote (update() only overwrites the cells in the
    range given, it never trims trailing ones). Resizing to the same row
    count the tab already has is a no-op API-side but skipped here anyway
    to avoid a pointless call on the common case where nothing moved.
    """
    target_row_count = len(rows) + 1  # +1 for the header
    if worksheet.row_count != target_row_count:
        worksheet.resize(rows=target_row_count)

    worksheet.update(
        range_name="A1",
        values=[HEADER] + rows,
        value_input_option="USER_ENTERED",
    )
    # Applied even when `rows` is empty (header only) -- a tab everything
    # just moved out of still gets its column formatting/filter kept ready
    # for whatever main.py's append_jobs writes into it next, the same way
    # a brand-new empty tab would.
    format_date_columns(worksheet)
    format_description_column(worksheet)
    set_basic_filter(worksheet)


def refresh_all() -> None:
    """Recompute every column and re-route rows across all ten tabs.

    Reads and reclassifies every tab in full before writing any of them --
    see module docstring for why (an interrupted per-tab read+move loop
    risks losing or duplicating a row; this doesn't remove anything from a
    tab until every row's destination across the whole spreadsheet is
    already known).
    """
    overrides = _fetch_overrides()
    company_type_by_firm, source_by_firm = _config_lookup_tables()
    spreadsheet = get_spreadsheet()

    worksheets: dict[str, gspread.Worksheet] = {}
    buckets: dict[str, list[dict[str, str]]] = {}
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            worksheets[tab] = get_worksheet(spreadsheet, tab)
            buckets[tab] = []

    seen_keys: dict[tuple[str, str], str] = {}
    moved = 0
    dropped_total = 0
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            origin_tab = tab_name(category, region)
            logger.info("full_refresh: reading %s", origin_tab)
            rows, dropped = _read_tab_rows(
                worksheets[origin_tab],
                company_type_by_firm,
                source_by_firm,
                overrides,
                category,
                region,
            )
            dropped_total += dropped
            for dest_tab, values in rows:
                key = (values["board"], values["job_id"])
                if key in seen_keys:
                    logger.warning(
                        "full_refresh: %s already placed on %s, skipping "
                        "duplicate seen again on %s",
                        key,
                        seen_keys[key],
                        origin_tab,
                    )
                    continue
                seen_keys[key] = dest_tab
                if dest_tab != origin_tab:
                    moved += 1
                buckets[dest_tab].append(values)

    for tab, worksheet in worksheets.items():
        logger.info("full_refresh: writing %s (%d rows)", tab, len(buckets[tab]))
        _write_tab(worksheet, [[values[name] for name in FIELDS] for values in buckets[tab]])

    logger.info(
        "full_refresh: done, %d row(s) moved to a different tab, %d row(s) "
        "dropped (excluded country)",
        moved,
        dropped_total,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    refresh_all()
