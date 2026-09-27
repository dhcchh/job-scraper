"""Tests for sheets.writer -- dedup-append/prune-closed logic, no live gspread/Google call.

Both functions take a gspread.Worksheet; here they're given a tiny fake with
just the methods writer.py actually calls, so the logic is exercised without
any real network call or credential.
"""

import datetime
from collections import Counter

import pytest

from jobscraper.models import Job
from jobscraper.sheets import writer


class _FakeWorksheet:
    title = "SWE/DE - PAC"

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


def test_append_jobs_refuses_a_reordered_header():
    # The 2026-09-21 incident: the sheet's header was migrated by unpushed
    # code, so CI appended rows shifted against it.
    header = list(reversed(writer.HEADER))
    worksheet = _FakeWorksheet([header, _row("acme", "1", "2026-09-01")])
    job = Job("acme", "2", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    with pytest.raises(RuntimeError, match="refusing to append or prune"):
        writer.append_jobs(worksheet, [job])

    assert worksheet.appended is None


def test_append_jobs_refuses_a_header_missing_a_field():
    worksheet = _FakeWorksheet([writer.HEADER[:-1]])
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    with pytest.raises(RuntimeError, match="SWE/DE - PAC"):
        writer.append_jobs(worksheet, [job])

    assert worksheet.appended is None


def test_append_jobs_tolerates_extra_columns_after_fields():
    header = writer.HEADER + ["notes", ""]
    worksheet = _FakeWorksheet([header, _row("acme", "1", "2026-09-01") + ["x", ""]])
    job = Job("acme", "2", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    writer.append_jobs(worksheet, [job])

    assert worksheet.appended == [job.to_row()]


def _job(board, job_id):
    return Job(board, job_id, "T", "D", "L", "U", "", datetime.date(2026, 9, 12))


def _prune(worksheet, current_jobs, fetched_boards, held_back=()):
    plan = writer.plan_prune(worksheet, current_jobs, fetched_boards)
    return writer.apply_prune(plan, held_back)


def test_prune_removes_postings_missing_from_current_fetch():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),  # closed -- not in today's fetch
        _row("acme", "2", "2026-08-20"),  # still open
        _row("acme", "3", "2026-09-12"),  # still open
    ]
    worksheet = _FakeWorksheet(rows)
    current_jobs = [_job("acme", "2"), _job("acme", "3")]

    pruned = _prune(worksheet, current_jobs, fetched_boards={"acme"})

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

    pruned = _prune(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.client.batch_update_calls == []
    assert pruned == 0


def test_prune_leaves_unfetched_board_rows_alone():
    # "beta" wasn't fetched this run (failed, or removed from config) --
    # its rows must survive even though beta has no entries in current_jobs.
    rows = [writer.HEADER, _row("beta", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    _prune(worksheet, current_jobs=[], fetched_boards={"acme"})

    assert worksheet.client.batch_update_calls == []


def test_prune_a_board_with_zero_current_postings_removes_all_its_rows():
    # acme fetched successfully but currently has no open postings at all --
    # every existing acme row should be treated as closed.
    rows = [writer.HEADER, _row("acme", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    _prune(worksheet, current_jobs=[], fetched_boards={"acme"})

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

    _prune(
        worksheet, current_jobs=[_job("acme", "3")], fetched_boards={"acme"}
    )

    (spreadsheet_id, body), = worksheet.client.batch_update_calls
    assert spreadsheet_id == "sheet-xyz"
    ranges = [request["deleteDimension"]["range"] for request in body["requests"]]
    assert ranges == [
        {"sheetId": 123, "dimension": "ROWS", "startIndex": 2, "endIndex": 3},
        {"sheetId": 123, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
    ]


def test_prune_refuses_a_mismatched_header():
    worksheet = _FakeWorksheet([["job_id"], ["1"]])
    with pytest.raises(RuntimeError, match="refusing to append or prune"):
        _prune(worksheet, current_jobs=[], fetched_boards={"acme"})
    assert worksheet.client.batch_update_calls == []


def test_row_key_tolerates_malformed_row():
    assert writer._row_key(["a"]) is None


def test_plan_prune_counts_every_tracked_row_per_board():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),  # closed
        _row("acme", "2", "2026-08-20"),  # still open
        _row("beta", "1", "2026-01-01"),  # beta not fetched -- never closed
    ]
    worksheet = _FakeWorksheet(rows)

    plan = writer.plan_prune(worksheet, [_job("acme", "2")], fetched_boards={"acme"})

    assert plan.closed_rows == {"acme": [1]}
    assert plan.tracked_counts == {"acme": 2, "beta": 1}
    assert worksheet.client.batch_update_calls == []  # planning never deletes


def test_apply_prune_keeps_held_back_firms_rows():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),
        _row("beta", "1", "2026-07-01"),
        _row("acme", "2", "2026-07-01"),
    ]
    worksheet = _FakeWorksheet(rows)

    pruned = _prune(
        worksheet, current_jobs=[], fetched_boards={"acme", "beta"}, held_back={"acme"}
    )

    assert pruned == 1
    (_spreadsheet_id, body), = worksheet.client.batch_update_calls
    ranges = [request["deleteDimension"]["range"] for request in body["requests"]]
    assert ranges == [
        {"sheetId": 123, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}
    ]


def test_apply_prune_noop_when_every_closed_row_is_held_back():
    worksheet = _FakeWorksheet([writer.HEADER, _row("acme", "1", "2026-07-01")])

    pruned = _prune(worksheet, current_jobs=[], fetched_boards={"acme"}, held_back={"acme"})

    assert pruned == 0
    assert worksheet.client.batch_update_calls == []


def _plan(closed_rows, tracked_counts):
    return writer.PrunePlan(_FakeWorksheet(), closed_rows, Counter(tracked_counts))


def test_held_back_prunes_holds_a_firm_whose_board_returned_nothing():
    # Even one row: a board with zero postings of any kind (noise included)
    # is far likelier broken than empty.
    plans = [_plan({"acme": [1]}, {"acme": 1})]

    held_back = writer.held_back_prunes(plans, fetched_counts={}, appended_counts={})

    assert held_back == {
        "acme": "board returned 0 postings; would prune all 1 tracked row(s)"
    }


def test_held_back_prunes_holds_a_majority_drop_summed_across_tabs():
    plans = [
        _plan({"acme": [1, 2, 3]}, {"acme": 4}),
        _plan({"acme": [5, 6, 7]}, {"acme": 6}),
    ]

    held_back = writer.held_back_prunes(
        plans, fetched_counts={"acme": 40}, appended_counts={}
    )

    assert held_back == {"acme": "would prune 6 of 10 tracked row(s)"}


def test_held_back_prunes_ignores_rows_appended_this_run():
    # A job_id format change: all 6 old rows look closed, and the same 6
    # postings were just appended under new ids -- still 6 of 6, not 6 of 12.
    plans = [_plan({"acme": [1, 2, 3, 4, 5, 6]}, {"acme": 12})]

    held_back = writer.held_back_prunes(
        plans, fetched_counts={"acme": 6}, appended_counts={"acme": 6}
    )

    assert held_back == {"acme": "would prune 6 of 6 tracked row(s)"}


def test_held_back_prunes_allows_ordinary_closures():
    plans = [
        _plan({"acme": [1, 2, 3, 4, 5]}, {"acme": 10}),  # exactly half
        _plan({"beta": [1, 2, 3, 4]}, {"beta": 4}),  # all, but under the minimum
        _plan({}, {"gamma": 3}),  # nothing closed
    ]

    held_back = writer.held_back_prunes(
        plans,
        fetched_counts={"acme": 20, "beta": 1, "gamma": 3},
        appended_counts={},
    )

    assert held_back == {}


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
        "L2:L",
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
    assert formatted_ranges == [("N2:N", {"wrapStrategy": "CLIP"})]


def test_format_description_column_tolerates_worksheet_without_format():
    class _WorksheetWithoutFormat:
        pass

    ws = _WorksheetWithoutFormat()
    writer.format_description_column(ws)  # does not raise


def test_set_basic_filter_calls_set_basic_filter_when_available():
    calls = []

    class _WorksheetWithFilter:
        def set_basic_filter(self, range_name):
            calls.append(range_name)

    writer.set_basic_filter(_WorksheetWithFilter())
    assert calls == ["A1:N"]


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
