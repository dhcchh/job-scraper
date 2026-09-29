"""Recompute sheet rows and route them to their current tabs."""

import datetime
import logging

from jobscraper.quality import validate_rows

import gspread

from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    is_excluded,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.models import FIELDS, Job, escape_sheet_row
from jobscraper.sheets.backfill import (
    _config_lookup_tables,
    _fetch_overrides,
    _recompute_row_values,
    _tab_rows_for_refresh,
)
from jobscraper.sheets.writer import (
    HEADER,
    format_tab,
    get_spreadsheet,
    get_worksheet,
)

logger = logging.getLogger(__name__)


def _classify(values: dict[str, str]) -> str | None:
    """Classify a recomputed row."""
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
    """Read, recompute, and route the rows from one tab."""
    loaded = _tab_rows_for_refresh(worksheet)
    if loaded is None:
        return [], 0
    data_rows, old_index, url_formula_cells = loaded

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
    """Rewrite a tab, shrinking its grid only after the update succeeds."""
    validate_rows(rows)
    values = [HEADER] + [escape_sheet_row(row) for row in rows]
    worksheet.update(
        range_name="A1",
        values=values,
        value_input_option="USER_ENTERED",
    )
    target_row_count = len(values)
    if worksheet.row_count != target_row_count:
        worksheet.resize(rows=target_row_count)
    format_tab(worksheet)


def refresh_all() -> None:
    """Recompute and route every managed sheet row."""
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
