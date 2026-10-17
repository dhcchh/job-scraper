"""Tests for sheets.full_refresh using fake integrations."""

import datetime
import logging

import pytest

from jobscraper.models import FIELDS
from jobscraper.sheets import full_refresh


class _FakeWorksheet:
    def __init__(self, rows=None, row_count=None):
        self.id = 0
        self.col_count = len(FIELDS)
        self._rows = rows or []
        self.row_count = row_count if row_count is not None else len(self._rows)
        self.updated_with = None

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
    def __init__(self, worksheets):
        self.worksheets = worksheets
        self.batches = []
        self.fail = False

    def batch_update(self, body):
        self.batches.append(body)
        if self.fail:
            raise RuntimeError("batch rejected")
        by_id = {ws.id: ws for ws in self.worksheets.values()}
        for request in body["requests"]:
            if "updateSheetProperties" in request:
                props = request["updateSheetProperties"]["properties"]
                by_id[props["sheetId"]].resize(rows=props["gridProperties"]["rowCount"])
            elif "updateCells" in request:
                update = request["updateCells"]
                rows = []
                for row in update["rows"]:
                    values = []
                    for cell in row["values"]:
                        value = cell["userEnteredValue"]
                        if "numberValue" in value:
                            values.append((datetime.date(1899, 12, 30) + datetime.timedelta(days=value["numberValue"])).isoformat())
                        else:
                            values.append(value.get("stringValue", value.get("formulaValue")))
                    rows.append(values)
                by_id[update["start"]["sheetId"]].updated_with = rows


def _patch_tabs(monkeypatch, worksheets):
    for index, worksheet in enumerate(worksheets.values()):
        worksheet.id = index
    spreadsheet = _FakeSpreadsheet(worksheets)
    monkeypatch.setattr(full_refresh, "get_spreadsheet", lambda: spreadsheet)
    monkeypatch.setattr(
        full_refresh, "get_worksheet", lambda spreadsheet, name: worksheets[name]
    )
    return spreadsheet


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


def test_route_sends_a_normal_title_to_its_tab():
    values = {
        "board": "Acme",
        "job_id": "1",
        "title": "Software Engineer",
        "department": "Eng",
        "location": "Singapore",
        "company_type": "",
    }
    assert full_refresh._route(full_refresh._as_job(values)) == "SWE/DE - PAC"


def test_route_uses_the_rows_company_type():
    # A bare research title is quant research only at a trading firm.
    values = {
        "board": "AQR",
        "job_id": "1",
        "title": "2027 Research Summer Analyst",
        "department": "",
        "location": "Greenwich, CT",
        "company_type": "Prop Trading/Hedge Fund",
    }
    assert full_refresh._route(full_refresh._as_job(values)) == "QT/QR - RoW"
    fintech = {**values, "company_type": "Fintech"}
    assert full_refresh._route(full_refresh._as_job(fintech)) is None


def test_rewrite_requests_preserve_literal_text_and_url_formulas():
    worksheet = _FakeWorksheet(row_count=1)
    row = _row(
        title="=SUM(1,2)", description="\t=1+1",
        url='=HYPERLINK("https://x/1", "Apply")',
    )

    requests = full_refresh._rewrite_requests(worksheet, [row])
    written = dict(zip(FIELDS, requests[1]["updateCells"]["rows"][1]["values"]))
    assert written["title"]["userEnteredValue"] == {"stringValue": "=SUM(1,2)"}
    assert written["description"]["userEnteredValue"] == {"stringValue": "\t=1+1"}
    assert written["url"]["userEnteredValue"] == {"formulaValue": '=HYPERLINK("https://x/1", "Apply")'}


def test_route_drops_a_recruiter_title():
    values = {
        "board": "Acme",
        "job_id": "1",
        "title": "Technical Recruiter",
        "department": "People",
        "location": "Singapore",
        "company_type": "",
    }
    assert full_refresh._route(full_refresh._as_job(values)) is None


def test_read_tab_rows_keeps_a_row_on_its_current_tab_when_unchanged():
    worksheet = _FakeWorksheet([list(FIELDS), _row(title="Software Engineer", location="Singapore")])

    rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

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

    rows, _dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

    dest, _values = rows[0]
    assert dest == "SWE/DE - PAC"


def test_read_tab_rows_moves_a_row_whose_category_changed():
    # Sitting under SWE/DE, but its title is really a QT/QR posting --
    # e.g. classify_roles.CATEGORIES picked up a new keyword since this row
    # was first written.
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Quantitative Researcher", location="Singapore")]
    )

    rows, _dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

    dest, _values = rows[0]
    assert dest == "QT/QR - PAC"


def test_read_tab_rows_drops_a_row_with_no_category(caplog):
    # Title no longer matches anything (e.g. now reads as a recruiter
    # title). Kept, the next daily prune would delete it as a closure.
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Technical Recruiter", location="Singapore")]
    )

    with caplog.at_level(logging.WARNING):
        rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

    assert rows == []
    assert dropped == 1
    assert "dropping" in caplog.text
    assert "no longer matches any category" in caplog.text


def test_read_tab_rows_drops_a_row_wholly_in_an_excluded_country(caplog):
    worksheet = _FakeWorksheet(
        [list(FIELDS), _row(title="Software Engineer", location="Hanoi, Vietnam")]
    )

    with caplog.at_level(logging.WARNING):
        rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

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

    rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})

    assert dropped == 0
    dest, _values = rows[0]
    assert dest == "SWE/DE - PAC"


def test_read_tab_rows_empty_sheet_returns_nothing():
    assert full_refresh._read_tab_rows(_FakeWorksheet([]), {}, {}, {}) == (
        [],
        0,
    )


def test_read_tab_rows_header_only_returns_nothing():
    worksheet = _FakeWorksheet([list(FIELDS)])
    assert full_refresh._read_tab_rows(worksheet, {}, {}, {}) == ([], 0)


@pytest.mark.parametrize("old_count, jobs", [(1, 2), (5, 0), (2, 1)])
def test_rewrite_requests_resize_and_write_in_the_same_batch(old_count, jobs):
    worksheet = _FakeWorksheet(row_count=old_count)
    requests = full_refresh._rewrite_requests(worksheet, [_row(job_id=str(i)) for i in range(jobs)])
    assert requests[0]["updateSheetProperties"]["properties"]["gridProperties"]["rowCount"] == jobs + 1
    assert len(requests[1]["updateCells"]["rows"]) == jobs + 1


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


def test_rejected_atomic_refresh_keeps_source_and_destination_unchanged(monkeypatch):
    _empty_boards(monkeypatch)
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))
    source = _FakeWorksheet([list(FIELDS), _row(location="London, United Kingdom")])
    destination = _FakeWorksheet([list(FIELDS)])
    worksheets = _all_tab_worksheets({"SWE/DE - PAC": source, "SWE/DE - RoW": destination})
    spreadsheet = _patch_tabs(monkeypatch, worksheets)
    spreadsheet.fail = True
    with pytest.raises(RuntimeError, match="batch rejected"):
        full_refresh.refresh_all()
    assert len(spreadsheet.batches) == 1
    written_ids = {
        request["updateCells"]["start"]["sheetId"]
        for request in spreadsheet.batches[0]["requests"] if "updateCells" in request
    }
    assert written_ids == {ws.id for ws in worksheets.values()}
    assert source.updated_with is None
    assert destination.updated_with is None
    assert len(source._rows) == 2


def test_invalid_row_in_later_tab_prevents_every_write(monkeypatch):
    _empty_boards(monkeypatch)
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))
    worksheets = _all_tab_worksheets({
        "SWE/DE - PAC": _FakeWorksheet([list(FIELDS), _row()]),
        "SRE/DevOps - RoW": _FakeWorksheet([list(FIELDS), _row(job_id="")]),
    })
    spreadsheet = _patch_tabs(monkeypatch, worksheets)
    with pytest.raises(ValueError, match="job_id"):
        full_refresh.refresh_all()
    assert spreadsheet.batches == []


def test_atomic_cell_values_preserve_dates_and_text_ids():
    assert full_refresh._cell_value("scraped_date", "2026-09-30") == {
        "numberValue": (datetime.date(2026, 9, 30) - datetime.date(1899, 12, 30)).days
    }
    assert full_refresh._cell_value("job_id", "000123") == {"stringValue": "000123"}
    assert full_refresh._cell_value("title", "=1+1") == {"stringValue": "=1+1"}


def test_excluded_country_is_dropped_even_when_title_is_unmatched():
    worksheet = _FakeWorksheet([list(FIELDS), _row(title="Recruiter", location="Noida, India")])
    rows, dropped = full_refresh._read_tab_rows(worksheet, {}, {}, {})
    assert rows == []
    assert dropped == 1


def _open_job(job_id, title="Software Engineer", location="Singapore", board="Acme"):
    from jobscraper.models import Job

    return Job(
        board, job_id, title, "Eng", location, f"https://x/{job_id}", "",
        datetime.date(2026, 10, 17), first_published="2026-05-01",
    )


def test_read_tab_rows_routes_by_the_open_postings_current_title():
    # Retitled since the row was written: route as the daily scrape will.
    worksheet = _FakeWorksheet([list(FIELDS), _row(title="Software Engineer")])
    overrides = {("Acme", "1"): _open_job("1", title="Quantitative Researcher")}

    rows, _dropped = full_refresh._read_tab_rows(worksheet, {}, {}, overrides)

    dest, values = rows[0]
    assert dest == "QT/QR - PAC"
    assert values["title"] == "Quantitative Researcher"


def test_refresh_all_adds_untracked_open_postings_that_now_route(monkeypatch, caplog):
    # A classifier change newly matches open postings. Adding them here keeps
    # the next daily scrape from announcing them as new.
    monkeypatch.setattr(full_refresh, "_config_lookup_tables", lambda: ({}, {}))
    monkeypatch.setattr(full_refresh, "_fetch_overrides", lambda: {
        ("Acme", "1"): _open_job("1"),  # already tracked
        ("Acme", "2"): _open_job("2", title="=Quant Trader"),  # newly matched
        ("Acme", "3"): _open_job("3", title="Technical Recruiter"),  # noise
        ("Acme", "4"): _open_job("4", location="Hanoi, Vietnam"),  # excluded
    })
    tracked = _FakeWorksheet([list(FIELDS), _row(board="Acme", job_id="1")], row_count=2)
    quant = _FakeWorksheet(row_count=1)
    worksheets = _all_tab_worksheets({"SWE/DE - PAC": tracked, "QT/QR - PAC": quant})
    _patch_tabs(monkeypatch, worksheets)

    with caplog.at_level(logging.INFO):
        full_refresh.refresh_all()

    assert len(tracked.updated_with) == 2  # header + the tracked row, once
    assert len(quant.updated_with) == 2
    added = dict(zip(FIELDS, quant.updated_with[1]))
    assert added["job_id"] == "2"
    assert added["title"] == "=Quant Trader"  # typed string, not a formula
    assert added["first_published"] == "2026-05-01"
    assert added["scraped_date"] == "2026-10-17"
    assert added["url"] == '=HYPERLINK("https://x/2", "Apply")'
    written = sum(len(ws.updated_with) - 1 for ws in worksheets.values() if ws.updated_with)
    assert written == 2
    assert "1 untracked open posting(s) added" in caplog.text
