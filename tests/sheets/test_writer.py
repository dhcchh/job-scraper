"""Tests for sheets.writer -- dedup-append/prune-closed logic, no live gspread/Google call.

Both functions take a gspread.Worksheet; here they're given a tiny fake with
just the methods writer.py actually calls, so the logic is exercised without
any real network call or credential.
"""

import datetime

import pytest

from jobscraper.models import Job
from jobscraper.sheets import writer


class _FakeWorksheet:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.appended = None
        self.updated_with = None
        self.id = 123
        self.spreadsheet_id = "sheet-xyz"
        self.client = _FakeHttpClient()

    def get_all_values(self):
        return self._rows

    def append_rows(self, rows, value_input_option=None):
        self.appended = rows

    def update(self, values, value_input_option=None):
        self.updated_with = values


class _FakeHttpClient:
    """Stands in for gspread's HTTPClient -- writer.py calls batch_update on
    it directly (worksheet.client.batch_update(worksheet.spreadsheet_id,
    body)), the same pattern gspread's own Worksheet.delete_dimension uses
    internally, rather than through the soft-deprecated Worksheet.spreadsheet
    accessor.
    """

    def __init__(self):
        self.batch_update_calls = []

    def batch_update(self, spreadsheet_id, body):
        self.batch_update_calls.append((spreadsheet_id, body))


def _row(board, job_id, scraped_date):
    # Built by column name, not position, so it doesn't need updating every
    # time FIELDS' column order changes (see models.FIELDS' docstring).
    values = {
        "board": board,
        "job_id": job_id,
        "title": "Title",
        "department": "Dept",
        "location": "Remote",
        "url": "https://x",
        "updated_at": "",
        "first_published": "",
        "scraped_date": scraped_date,
        "seniority": "Unspecified",
        "company_type": "",
        "source": "",
        "description": "",
        "salary": "",
    }
    return [values[name] for name in writer.HEADER]


def test_append_jobs_writes_job_rows():
    worksheet = _FakeWorksheet()
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [job])

    assert worksheet.appended == [job.to_row()]
    assert appended == [job]


def test_append_jobs_noop_on_empty_list():
    worksheet = _FakeWorksheet()
    appended = writer.append_jobs(worksheet, [])
    assert worksheet.appended is None
    assert appended == []


def test_append_jobs_skips_already_tracked_job():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [job])

    assert worksheet.appended is None  # already tracked -- not re-appended
    assert appended == []


def test_append_jobs_appends_only_the_new_ones():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    seen = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))
    new = Job("acme", "2", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [seen, new])

    assert worksheet.appended == [new.to_row()]
    assert appended == [new]


def test_append_jobs_same_job_id_different_board_is_not_a_duplicate():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    job = Job("beta", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    writer.append_jobs(worksheet, [job])

    assert worksheet.appended == [job.to_row()]


def _job(board, job_id):
    return Job(board, job_id, "T", "D", "L", "U", "", datetime.date(2026, 9, 12))


def test_prune_removes_postings_missing_from_current_fetch():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),  # closed -- not in today's fetch
        _row("acme", "2", "2026-08-20"),  # still open
        _row("acme", "3", "2026-09-12"),  # still open
    ]
    worksheet = _FakeWorksheet(rows)
    current_jobs = [_job("acme", "2"), _job("acme", "3")]

    pruned = writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.updated_with is None
    assert worksheet.client.batch_update_calls == [
        (
            "sheet-xyz",
            {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": 123,
                                "dimension": "ROWS",
                                "startIndex": 1,
                                "endIndex": 2,
                            }
                        }
                    }
                ]
            },
        )
    ]
    assert pruned == 1


def test_prune_noop_when_nothing_closed():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-12")]
    worksheet = _FakeWorksheet(rows)
    current_jobs = [_job("acme", "1")]

    pruned = writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.client.batch_update_calls == []
    assert pruned == 0


def test_prune_leaves_unfetched_board_rows_alone():
    # "beta" wasn't fetched this run (failed, or removed from config) --
    # its rows must survive even though beta has no entries in current_jobs.
    rows = [writer.HEADER, _row("beta", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards={"acme"})

    assert worksheet.client.batch_update_calls == []


def test_prune_a_board_with_zero_current_postings_removes_all_its_rows():
    # acme fetched successfully but currently has no open postings at all --
    # every existing acme row should be treated as closed.
    rows = [writer.HEADER, _row("acme", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards={"acme"})

    assert worksheet.client.batch_update_calls == [
        (
            "sheet-xyz",
            {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": 123,
                                "dimension": "ROWS",
                                "startIndex": 1,
                                "endIndex": 2,
                            }
                        }
                    }
                ]
            },
        )
    ]


def test_prune_deletes_multiple_rows_from_bottom_to_top():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),
        _row("acme", "2", "2026-07-01"),
        _row("acme", "3", "2026-09-12"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.prune_closed_postings(
        worksheet, current_jobs=[_job("acme", "3")], fetched_boards={"acme"}
    )

    (spreadsheet_id, body), = worksheet.client.batch_update_calls
    assert spreadsheet_id == "sheet-xyz"
    ranges = [request["deleteDimension"]["range"] for request in body["requests"]]
    assert ranges == [
        {"sheetId": 123, "dimension": "ROWS", "startIndex": 2, "endIndex": 3},
        {"sheetId": 123, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
    ]


def test_prune_missing_board_column_raises():
    worksheet = _FakeWorksheet([["job_id"]])
    with pytest.raises(ValueError):
        writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards=set())


def test_row_key_tolerates_malformed_row():
    assert writer._row_key(["a"], board_index=0, job_id_index=5) is None


def test_format_date_columns_calls_format_when_available():
    formatted_ranges = []

    class _WorksheetWithFormat:
        def format(self, range_name, format_dict):
            formatted_ranges.append((range_name, format_dict))

    ws = _WorksheetWithFormat()
    writer.format_date_columns(ws)
    assert len(formatted_ranges) == 2
    assert formatted_ranges[0] == (
        "A2:B",
        {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}},
    )
    assert formatted_ranges[1] == (
        "K2:K",
        {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}},
    )


def test_format_date_columns_tolerates_worksheet_without_format():
    class _WorksheetWithoutFormat:
        pass

    ws = _WorksheetWithoutFormat()
    writer.format_date_columns(ws)  # does not raise


def test_format_description_column_clips_instead_of_wrapping():
    formatted_ranges = []

    class _WorksheetWithFormat:
        def format(self, range_name, format_dict):
            formatted_ranges.append((range_name, format_dict))

    ws = _WorksheetWithFormat()
    writer.format_description_column(ws)
    assert formatted_ranges == [("M2:M", {"wrapStrategy": "CLIP"})]


def test_format_description_column_tolerates_worksheet_without_format():
    class _WorksheetWithoutFormat:
        pass

    ws = _WorksheetWithoutFormat()
    writer.format_description_column(ws)  # does not raise


def test_set_basic_filter_calls_set_basic_filter_when_available():
    calls = []

    class _WorksheetWithFilter:
        def set_basic_filter(self):
            calls.append(True)

    writer.set_basic_filter(_WorksheetWithFilter())
    assert calls == [True]


def test_set_basic_filter_tolerates_worksheet_without_it():
    class _WorksheetWithoutFilter:
        pass

    writer.set_basic_filter(_WorksheetWithoutFilter())  # does not raise


class _FakeSpreadsheet:
    def __init__(self, title):
        self.title = title


def test_check_expected_title_passes_when_unset(monkeypatch):
    monkeypatch.setattr(writer.config, "GOOGLE_SHEET_TITLE", None)
    writer._check_expected_title(_FakeSpreadsheet("Anything"))  # does not raise


def test_check_expected_title_passes_when_matching(monkeypatch):
    monkeypatch.setattr(writer.config, "GOOGLE_SHEET_TITLE", "Job Board Tracker")
    writer._check_expected_title(_FakeSpreadsheet("Job Board Tracker"))  # does not raise


def test_check_expected_title_raises_on_mismatch(monkeypatch):
    monkeypatch.setattr(writer.config, "GOOGLE_SHEET_TITLE", "Job Board Tracker")
    with pytest.raises(RuntimeError, match="Some Other Sheet"):
        writer._check_expected_title(_FakeSpreadsheet("Some Other Sheet"))


