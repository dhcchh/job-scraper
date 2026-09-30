"""Refresh existing sheet rows from current config and board data."""

import logging
import re

from jobscraper.quality import validate_rows

import gspread
from gspread.utils import rowcol_to_a1

from jobscraper import config
from jobscraper.enrichment.classify_region import REGIONS, tab_name
from jobscraper.enrichment.classify_roles import CATEGORIES
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_locations
from jobscraper.job_boards import fetchers
from jobscraper.models import (
    FIELDS,
    FIRMS_NEEDING_DESCRIPTION,
    Job,
    escape_sheet_row,
    hyperlink_formula,
    normalize_date,
)
from jobscraper.sheets.writer import (
    HEADER,
    format_tab,
    get_spreadsheet,
    get_worksheet,
)

logger = logging.getLogger(__name__)

_HYPERLINK_RE = re.compile(
    r'^=HYPERLINK\(\s*"((?:[^"]|"")*)"\s*,\s*"(?:[^"]|"")*"\s*\)$', re.IGNORECASE
)


def _extract_url(cell: str) -> str:
    """Extract the URL from a Sheets hyperlink formula or return the cell unchanged."""
    match = _HYPERLINK_RE.match(cell.strip())
    if not match:
        return cell
    return match.group(1).replace('""', '"')


def _url_formula_cells(
    worksheet: gspread.Worksheet, header: list[str], num_rows: int
) -> list[str]:
    """Read formula text from the URL column."""
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
    """Read a tab's data rows, column indexes, and URL formulas."""
    all_values = worksheet.get_all_values()
    if not all_values:
        return None
    header, *data_rows = all_values
    old_index = {
        name: (header.index(name) if name in header else None) for name in FIELDS
    }
    url_formula_cells = _url_formula_cells(worksheet, header, len(data_rows))
    return data_rows, old_index, url_formula_cells


def _fetch_overrides() -> dict[tuple[str, str], Job]:
    """Fetch open jobs keyed by `(board, job_id)` for refresh overrides."""
    overrides: dict[tuple[str, str], Job] = {}
    for result in fetchers.fetch_boards(fetchers.board_sources()):
        if result.error is not None:
            logger.warning(
                "refresh: couldn't re-fetch %s for location/first_published"
                "/description overrides, its rows keep whatever they "
                "already have",
                result.board["firm"],
            )
            continue
        for job in result.jobs:
            overrides[(job.board, job.job_id)] = job
    return overrides


def _renormalize_location(value: str) -> str:
    """Re-normalize an existing pipe-delimited location cell."""
    pieces = [p.strip() for p in value.split(" | ") if p.strip()]
    return normalize_locations(pieces) if pieces else value


def _config_lookup_tables() -> tuple[dict[str, str], dict[str, str]]:
    """Build current company-type and board-source lookups by firm."""
    company_type_by_firm = {
        board["firm"]: board["company_type"]
        for _source, _label, board_list in config.board_sources()
        for board in board_list
    }
    source_by_firm = {
        board["firm"]: source
        for _source, source, board_list in config.board_sources()
        for board in board_list
    }
    return company_type_by_firm, source_by_firm


def _read_row_values(row: list[str], old_index: dict[str, int | None]) -> dict[str, str]:
    """Read every managed field from a possibly short or older row."""
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
    """Recompute one row using current config, classifiers, and overrides."""

    values = _read_row_values(row, old_index)

    fresh = overrides.get((values["board"], values["job_id"])) if overrides else None

    values["seniority"] = (
        fresh.seniority
        if fresh is not None
        else classify_seniority(values["title"], values["department"])
    )

    new_company_type = company_type_by_firm.get(values["board"])
    if new_company_type is not None:
        values["company_type"] = new_company_type

    new_source = source_by_firm.get(values["board"])
    if new_source is not None:
        values["source"] = new_source

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
    """Recompute and rewrite every row in one worksheet."""
    loaded = _tab_rows_for_refresh(worksheet)
    if loaded is None:
        loaded = ([], {name: None for name in FIELDS}, [])
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

    validate_rows(new_rows)
    worksheet.update(
        range_name="A1",
        values=[HEADER] + [escape_sheet_row(row) for row in new_rows],
        value_input_option="USER_ENTERED",
    )
    format_tab(worksheet)
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
