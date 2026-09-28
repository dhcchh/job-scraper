"""Tests for sheets.full_refresh -- no live gspread/Google call.

Same self-contained fake-worksheet approach as test_writer.py/test_backfill.py
(see CLAUDE.md's Architecture section on why sheets tests do this instead of
sharing one fixture).
"""

import logging

from jobscraper.models import FIELDS
from jobscraper.sheets import full_refresh


class _FakeWorksheet:
    def __init__(self, rows=None, row_count=None):
        self._rows = rows or []
        self.row_count = row_count if row_count is not None else len(self._rows)
        self.updated_with = None
        self.resized_to = None

    def get_all_values(self):
        return self._rows

    def get(self, range_name, value_render_option=None, pad_values=None, maintain_size=None):
        header, *data_rows = self._rows
        url_index = header.index("url")
        return [
            [row[url_index]] if url_index < len(row) and row[url_index] else []
            for row in data_rows
        ]

    def update(self, values, range_name=None, value_input_option=None):
        self.updated_with = values

    def resize(self, rows=None, cols=None):
        self.resized_to = rows
        self.row_count = rows


def _row(
    board="Acme",
    job_id="1",
    title="Software Engineer",
    location="Singapore",
    department="Eng",
    url="https://x/1",
    updated_at="2026-09-01",
    scraped_date="2026-09-01",
    seniority="Unspecified",
    company_type="",
    first_published="",
    source="",
    description="",
    salary="",
):
    values = {
        "first_published": first_published,
        "updated_at": updated_at,
        "board": board,
        "title": title,
        "location": location,
        "seniority": seniority,
        "company_type": company_type,
        "url": url,
        "department": department,
        "job_id": job_id,
        "scraped_date": scraped_date,
        "source": source,
        "description": description,
        "salary": salary,
    }
    return [values[name] for name in FIELDS]


def _empty_boards(monkeypatch):
    # No boards to re-fetch for location/first_published/description
    # overrides -- keeps this off the network, same convention as
    # test_backfill.py's test_refresh_all_refreshes_every_category_region_tab.
    monkeypatch.setattr(full_refresh, "_fetch_overrides", lambda: {})


class _FakeSpreadsheet:
    pass


def _patch_tabs(monkeypatch, worksheets):
    monkeypatch.setattr(full_refresh, "get_spreadsheet", lambda: _FakeSpreadsheet())
    monkeypatch.setattr(
        full_refresh, "get_worksheet", lambda spreadsheet, name: worksheets[name]
    )


def _all_tab_worksheets(overrides=None):
    """Every (category, region) tab, empty by default, overridden by name."""
    from jobscraper.enrichment.classify_region import REGIONS, tab_name
    from jobscraper.enrichment.classify_roles import CATEGORIES

    worksheets = {
        tab_name(category, region): _FakeWorksheet(row_count=1)
        for category, _keywords in CATEGORIES
        for region in REGIONS
    }
    worksheets.update(overrides or {})
    return worksheets


def test_classify_returns_category_for_a_normal_title():
    values = {
        "board": "Acme",
        "job_id": "1",
        "title": "Software Engineer",
        "department": "Eng",
        "location": "Singapore",
    }
    assert full_refresh._classify(values) == "SWE/DE"


def test_classify_returns_none_for_a_recruiter_title():
    values = {
        "board": "Acme",
        "job_id": "1",
        "title": "Technical Recruiter",
        "department": "People",
        "location": "Singapore",
    }
    assert full_refresh._classify(values) is None


def test_read_tab_rows_keeps_a_row_on_its_current_tab_when_unchanged():
    worksheet = _FakeWorksheet([list(FIELDS), _row(title="Software Engineer", location="Singapore")])

    rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "PAC")

    assert dropped == 0
    assert len(rows) == 1
    dest, values = rows[0]
    assert dest == "SWE/DE - PAC"
    assert values["board"] == "Acme"


def test_read_tab_rows_moves_a_row_whose_region_changed():
    # Sitting in the RoW tab (stale -- written before Thailand joined PAC),
    # but Bangkok now classifies as PAC -- see classify_region.py.
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Software Engineer", location="Bangkok, Thailand")]
    )

    rows, _dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "RoW")

    dest, _values = rows[0]
    assert dest == "SWE/DE - PAC"


def test_read_tab_rows_moves_a_row_whose_category_changed():
    # Sitting under SWE/DE, but its title is really a QT/QR posting --
    # e.g. classify_roles.CATEGORIES picked up a new keyword since this row
    # was first written.
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Quantitative Researcher", location="Singapore")]
    )

    rows, _dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "PAC")

    dest, _values = rows[0]
    assert dest == "QT/QR - PAC"


def test_read_tab_rows_leaves_a_row_with_no_category_on_its_origin_tab(caplog):
    # Title no longer matches anything (e.g. now reads as a recruiter
    # title) -- must not be dropped from the tracker entirely.
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Technical Recruiter", location="Singapore")]
    )

    with caplog.at_level(logging.WARNING):
        rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "PAC")

    assert dropped == 0
    dest, _values = rows[0]
    assert dest == "SWE/DE - PAC"
    assert "no longer matches any category" in caplog.text


def test_read_tab_rows_drops_a_row_wholly_in_an_excluded_country(caplog):
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Software Engineer", location="Hanoi, Vietnam")]
    )

    with caplog.at_level(logging.WARNING):
        rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "RoW")

    assert rows == []
    assert dropped == 1
    assert "dropping" in caplog.text
    assert "excluded country" in caplog.text


def test_read_tab_rows_keeps_a_multi_location_row_with_one_non_excluded_office():
    # Vietnam alone would be excluded, but Singapore is a real, keepable
    # office -- kept and classified normally (PAC wins), not dropped.
    worksheet = _FakeWorksheet(
        [
            list(FIELDS),
            _row(title="Software Engineer", location="Hanoi, Vietnam | Singapore"),
        ]
    )

    rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "RoW")

    assert dropped == 0
    dest, _values = rows[0]
    assert dest == "SWE/DE - PAC"


def test_read_tab_rows_empty_sheet_returns_nothing():
    assert full_refresh._read_tab_rows(_FakeWorksheet([]), {}, {}, {}, "SWE/DE", "PAC") == (
        [],
        0,
    )


def test_read_tab_rows_header_only_returns_nothing():
    worksheet = _FakeWorksheet([list(FIELDS)])
    assert full_refresh._read_tab_rows(worksheet, {}, {}, {}, "SWE/DE", "PAC") == ([], 0)


def test_write_tab_grows_the_grid_when_more_rows_than_before():
    worksheet = _FakeWorksheet(row_count=1)  # header only, currently 0 data rows

    full_refresh._write_tab(worksheet, [_row(job_id="a"), _row(job_id="b")])

    assert worksheet.resized_to == 3
    assert worksheet.updated_with[0] == list(FIELDS)
    assert worksheet.updated_with[1:] == [_row(job_id="a"), _row(job_id="b")]


def test_write_tab_shrinks_the_grid_when_fewer_rows_than_before():
    worksheet = _FakeWorksheet(row_count=5)  # header + 4 old data rows

    full_refresh._write_tab(worksheet, [])

    assert worksheet.resized_to == 1
    assert worksheet.updated_with == [list(FIELDS)]


def test_write_tab_skips_resize_when_row_count_already_matches():
    worksheet = _FakeWorksheet(row_count=2)  # header + 1 data row already

    full_refresh._write_tab(worksheet, [_row(job_id="a")])

    assert worksheet.resized_to is None
    assert worksheet.updated_with == [list(FIELDS), _row(job_id="a")]


def test_refresh_all_moves_a_row_between_tabs(monkeypatch):
    _empty_boards(monkeypatch)
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))

    row_source = _FakeWorksheet(
        [list(FIELDS), _row(board="Acme", job_id="1", location="Bangkok, Thailand")],
        row_count=2,
    )
    row_dest = _FakeWorksheet(row_count=1)
    worksheets = _all_tab_worksheets(
        {"SWE/DE - RoW": row_source, "SWE/DE - PAC": row_dest}
    )
    _patch_tabs(monkeypatch, worksheets)

    full_refresh.refresh_all()

    # Moved out of RoW (down to header-only)...
    assert row_source.updated_with == [list(FIELDS)]
    # ...and into PAC.
    assert row_dest.updated_with[0] == list(FIELDS)
    assert len(row_dest.updated_with) == 2
    board_index = FIELDS.index("board")
    assert row_dest.updated_with[1][board_index] == "Acme"


def test_refresh_all_dedupes_a_key_seen_on_two_tabs(monkeypatch, caplog):
    _empty_boards(monkeypatch)
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))

    # Same (board, job_id) sitting in two different tabs -- shouldn't
    # happen in normal operation, but must not be written to both.
    tab_a = _FakeWorksheet(
        [list(FIELDS), _row(board="Acme", job_id="1", location="Singapore")], row_count=2
    )
    tab_b = _FakeWorksheet(
        [list(FIELDS), _row(board="Acme", job_id="1", location="Singapore")], row_count=2
    )
    worksheets = _all_tab_worksheets({"DS/ML/Algos - PAC": tab_a, "SWE/DE - PAC": tab_b})
    _patch_tabs(monkeypatch, worksheets)

    with caplog.at_level(logging.WARNING):
        full_refresh.refresh_all()

    total_rows_written = sum(
        len(ws.updated_with) - 1 for ws in worksheets.values() if ws.updated_with
    )
    assert total_rows_written == 1
    assert "skipping duplicate" in caplog.text


def test_refresh_all_drops_a_row_wholly_in_an_excluded_country(monkeypatch, caplog):
    _empty_boards(monkeypatch)
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))

    row_source = _FakeWorksheet(
        [list(FIELDS), _row(board="Acme", job_id="1", location="Hanoi, Vietnam")],
        row_count=2,
    )
    worksheets = _all_tab_worksheets({"SWE/DE - RoW": row_source})
    _patch_tabs(monkeypatch, worksheets)

    with caplog.at_level(logging.WARNING):
        full_refresh.refresh_all()

    assert row_source.updated_with == [list(FIELDS)]
    total_rows_written = sum(
        len(ws.updated_with) - 1 for ws in worksheets.values() if ws.updated_with
    )
    assert total_rows_written == 0
    assert "excluded country" in caplog.text
