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
from jobscraper.models import DATE_FIELDS, FIELDS, Job
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
        company_type=values["company_type"],
    )
    return classify_job(stub)


def _read_tab_rows(
    worksheet: gspread.Worksheet,
    company_type_by_firm: dict[str, str],
    source_by_firm: dict[str, str],
    overrides: dict[tuple[str, str], Job],
) -> tuple[list[tuple[str, dict[str, str]]], int]:
    """Read, recompute, and route the rows from one tab.

    Rows the daily scrape would no longer route -- an unmatched title, or
    every office excluded -- are dropped. Kept, the next daily run would
    prune them anyway and count them as closures against the prune guard.
    """
    loaded = _tab_rows_for_refresh(worksheet)
    if loaded is None:
        return [], 0
    data_rows, old_index, url_formula_cells = loaded

    rows: list[tuple[str, dict[str, str]]] = []
    dropped = 0
    for row, url_cell in zip(data_rows, url_formula_cells):
        values = _recompute_row_values(
            row, url_cell, old_index, company_type_by_firm, source_by_firm, overrides
        )
        category = _classify(values)
        if category is None:
            reason = "its title no longer matches any category"
        elif is_excluded(values["location"]):
            reason = f"every office in {values['location']!r} is an excluded country"
        else:
            rows.append((tab_name(category, classify_region(values["location"])), values))
            continue
        logger.warning(
            "full_refresh: dropping %r (board=%s, job_id=%s) -- %s",
            values["title"],
            values["board"],
            values["job_id"],
            reason,
        )
        dropped += 1
    return rows, dropped


def _cell_value(field: str, value: str) -> dict:
    """Use explicit Sheets types to preserve identities, dates, and links."""
    if field == "url" and value:
        return {"formulaValue": value}
    if field in DATE_FIELDS and value:
        try:
            date = datetime.date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return {"numberValue": (date - datetime.date(1899, 12, 30)).days}
    return {"stringValue": value}


def _rewrite_requests(worksheet: gspread.Worksheet, rows: list[list[str]]) -> list[dict]:
    """Build a tab replacement for the spreadsheet's atomic batch."""
    validate_rows(rows)
    return [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "gridProperties": {
                        "rowCount": len(rows) + 1,
                        "columnCount": max(worksheet.col_count, len(FIELDS)),
                    },
                },
                "fields": "gridProperties.rowCount,gridProperties.columnCount",
            }
        },
        {
            "updateCells": {
                "start": {"sheetId": worksheet.id, "rowIndex": 0, "columnIndex": 0},
                "rows": [
                    {"values": [{"userEnteredValue": {"stringValue": name}} for name in HEADER]},
                    *(
                        {"values": [
                            {"userEnteredValue": _cell_value(field, value)}
                            for field, value in zip(FIELDS, row)
                        ]}
                        for row in rows
                    ),
                ],
                "fields": "userEnteredValue",
            }
        },
    ]


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

    requests = []
    for tab, worksheet in worksheets.items():
        requests.extend(_rewrite_requests(
            worksheet, [[values[name] for name in FIELDS] for values in buckets[tab]]
        ))
    # Every row and grid change is validated and committed in a single batch.
    # A rejected request cannot delete a source row without its destination.
    spreadsheet.batch_update({"requests": requests})
    for worksheet in worksheets.values():
        format_tab(worksheet)

    logger.info(
        "full_refresh: done, %d row(s) moved to a different tab, %d row(s) "
        "dropped (unmatched title or excluded country)",
        moved,
        dropped_total,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    refresh_all()
