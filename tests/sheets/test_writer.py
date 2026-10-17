"""Tests for sheets.writer using fake integrations."""

import datetime
from collections import Counter

import pytest

from jobscraper.models import Job
from jobscraper.sheets import writer


class _FakeWorksheet:
    title = "SWE/DE - PAC"

    def __init__(self, rows=None):
        self._rows = [writer.HEADER] if rows is None else rows
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

    def __init__(self, filter_view_ids=()):
        self.batch_update_calls = []
        self.filter_view_ids = filter_view_ids

    def batch_update(self, spreadsheet_id, body):
        self.batch_update_calls.append((spreadsheet_id, body))

    def fetch_sheet_metadata(self, spreadsheet_id, params=None):
        views = [{"filterViewId": view_id} for view_id in self.filter_view_ids]
        return {"sheets": [{"properties": {"sheetId": 123}, "filterViews": views}]}


def _deletion_calls(worksheet):
    """batch_update calls that delete rows -- the filter refresh after is separate."""
    return [
        call
        for call in worksheet.client.batch_update_calls
        if "deleteDimension" in call[1]["requests"][0]
    ]


def _row(board, job_id, scraped_date):
    # Built by column name, not position, so it doesn't need updating every
    # time FIELDS' column order changes.
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


def test_append_jobs_uses_given_existing_keys_instead_of_rereading():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    plan = writer.plan_prune(worksheet, [], fetched_boards=set())
    worksheet.get_all_values = lambda: pytest.fail("tab reread for append")
    seen = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))
    new = Job("acme", "2", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [seen, new], existing_keys=plan.keys)

    assert plan.keys == {("acme", "1")}
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
    assert _deletion_calls(worksheet) == [
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

    assert _deletion_calls(worksheet) == [
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

    (spreadsheet_id, body), = _deletion_calls(worksheet)
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
    (_spreadsheet_id, body), = _deletion_calls(worksheet)
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
    return writer.PrunePlan(
        _FakeWorksheet(), closed_rows, Counter(tracked_counts), frozenset()
    )


def test_held_back_prunes_holds_a_firm_whose_board_returned_nothing():
    # Even one row: a board with zero postings of any kind (noise included)
    # is far likelier broken than empty.
    plans = [_plan({"acme": [1]}, {"acme": 1})]

    held_back = writer.held_back_prunes(plans, fetched_counts={})

    assert held_back == {
        "acme": "board returned 0 postings; would prune all 1 tracked row(s)"
    }


def test_held_back_prunes_holds_a_majority_drop_summed_across_tabs():
    plans = [
        _plan({"acme": [1, 2, 3]}, {"acme": 4}),
        _plan({"acme": [5, 6, 7]}, {"acme": 6}),
    ]

    held_back = writer.held_back_prunes(
        plans, fetched_counts={"acme": 40}
    )

    assert held_back == {"acme": "would prune 6 of 10 tracked row(s)"}


def test_held_back_prunes_uses_counts_before_appending():
    # Six replacement IDs cannot dilute the baseline of six tracked rows.
    plans = [_plan({"acme": [1, 2, 3, 4, 5, 6]}, {"acme": 6})]

    held_back = writer.held_back_prunes(
        plans, fetched_counts={"acme": 6}
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
    )

    assert held_back == {}


def _date_format_request(column):
    return {
        "repeatCell": {
            "range": {
                "sheetId": 123,
                "startRowIndex": 1,
                "startColumnIndex": column,
                "endColumnIndex": column + 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}
                }
            },
            "fields": "userEnteredFormat(numberFormat)",
        }
    }


_WHOLE_TAB = {
    "sheetId": 123,
    "startRowIndex": 0,
    "startColumnIndex": 0,
    "endColumnIndex": len(writer.HEADER),
}


def test_format_tab_sends_every_managed_format_in_one_write():
    worksheet = _FakeWorksheet()
    worksheet.client = _FakeHttpClient(filter_view_ids=[7])

    writer.format_tab(worksheet)

    (spreadsheet_id, body), = worksheet.client.batch_update_calls
    assert spreadsheet_id == "sheet-xyz"
    assert body["requests"] == [
        _date_format_request(writer.HEADER.index("first_published")),  # A
        _date_format_request(writer.HEADER.index("updated_at")),  # B
        _date_format_request(writer.HEADER.index("scraped_date")),  # L
        {
            "repeatCell": {
                "range": {
                    "sheetId": 123,
                    "startRowIndex": 1,
                    "startColumnIndex": 13,  # N
                    "endColumnIndex": 14,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat(wrapStrategy)",
            }
        },
        {"setBasicFilter": {"filter": {"range": _WHOLE_TAB}}},
        {
            "updateFilterView": {
                "filter": {"filterViewId": 7, "range": _WHOLE_TAB},
                "fields": "range",
            }
        },
    ]


def test_format_tab_tolerates_worksheet_without_client():
    class _WorksheetWithoutClient:
        pass

    writer.format_tab(_WorksheetWithoutClient())  # does not raise


def test_set_basic_filter_is_one_write_including_saved_views():
    worksheet = _FakeWorksheet()
    worksheet.client = _FakeHttpClient(filter_view_ids=[7, 8])

    writer.set_basic_filter(worksheet)

    (_spreadsheet_id, body), = worksheet.client.batch_update_calls
    assert [next(iter(request)) for request in body["requests"]] == [
        "setBasicFilter",
        "updateFilterView",
        "updateFilterView",
    ]


def test_set_basic_filter_preserves_existing_settings_when_metadata_fails():
    worksheet = _FakeWorksheet()

    def failing_metadata(spreadsheet_id, params=None):
        raise RuntimeError("metadata down")

    worksheet.client.fetch_sheet_metadata = failing_metadata

    with pytest.raises(RuntimeError, match="metadata down"):
        writer.set_basic_filter(worksheet)
    assert worksheet.client.batch_update_calls == []


def test_set_basic_filter_tolerates_worksheet_without_client():
    class _WorksheetWithoutClient:
        pass

    writer.set_basic_filter(_WorksheetWithoutClient())  # does not raise


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


class _FakeResponse:
    def __init__(self, code):
        self.text = ""
        self._code = code

    def json(self):
        return {"error": {"code": self._code, "message": "x", "status": "x"}}


class _FakeHTMLResponse:
    """A Sheets 5xx error page: not JSON, so gspread reports code -1."""

    text = "<!DOCTYPE html><title>Error 502 (Server Error)!!1</title>"

    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        raise ValueError("not JSON")


def _quota_client(monkeypatch, failures):
    """A retrying client whose underlying requests fail in turn, then succeed.

    Each failure is an HTTP code for a JSON API error, or an exception to raise.
    """
    calls = []
    sleeps = []

    def fake_request(self, *args, **kwargs):
        calls.append(args)
        if len(calls) <= len(failures):
            failure = failures[len(calls) - 1]
            if isinstance(failure, BaseException):
                raise failure
            raise writer.APIError(_FakeResponse(failure))
        return "ok"

    monkeypatch.setattr(writer.gspread.HTTPClient, "request", fake_request)
    monkeypatch.setattr(writer.time, "sleep", sleeps.append)
    client = writer._RetryingHTTPClient.__new__(writer._RetryingHTTPClient)
    return client, calls, sleeps


def test_quota_client_retries_a_429_then_succeeds(monkeypatch):
    client, calls, sleeps = _quota_client(monkeypatch, [429, 429])

    assert client.request("post", "url") == "ok"
    assert len(calls) == 3
    assert sleeps == [10, 20]


def test_quota_client_gives_up_after_its_last_wait(monkeypatch):
    client, calls, sleeps = _quota_client(monkeypatch, [429] * 5)

    with pytest.raises(writer.APIError):
        client.request("post", "url")
    assert len(calls) == 5
    assert sleeps == list(writer._QUOTA_RETRY_WAITS)


@pytest.mark.parametrize("code", [500, 503, 403])
def test_quota_client_never_retries_other_errors(monkeypatch, code):
    # A 5xx append may already have landed -- retrying could duplicate rows.
    client, calls, sleeps = _quota_client(monkeypatch, [code])

    with pytest.raises(writer.APIError):
        client.request("post", "url")
    assert len(calls) == 1
    assert sleeps == []


def test_read_is_retried_after_an_html_5xx_page(monkeypatch):
    # 2026-10-06: a 502 page while fetching sheet metadata failed the run.
    error = writer.APIError(_FakeHTMLResponse(502))
    assert error.code == -1
    client, calls, sleeps = _quota_client(monkeypatch, [error])

    assert client.request("get", "url") == "ok"
    assert len(calls) == 2
    assert sleeps == [5]


@pytest.mark.parametrize("error", [
    # 2026-10-03: a values read was cut off mid-body (IncompleteRead).
    writer.requests.exceptions.ChunkedEncodingError("Connection broken"),
    writer.requests.exceptions.ConnectionError("reset"),
    writer.requests.exceptions.ReadTimeout("slow"),
])
def test_read_is_retried_after_a_dropped_connection(monkeypatch, error):
    client, calls, sleeps = _quota_client(monkeypatch, [error, 503])

    assert client.request("get", "url") == "ok"
    assert len(calls) == 3
    assert sleeps == [5, 15]


def test_read_gives_up_after_its_last_wait(monkeypatch):
    client, calls, sleeps = _quota_client(monkeypatch, [502] * 4)

    with pytest.raises(writer.APIError):
        client.request("get", "url")
    assert len(calls) == 4
    assert sleeps == list(writer._READ_RETRY_WAITS)


def test_read_still_gets_its_quota_retries(monkeypatch):
    client, calls, sleeps = _quota_client(monkeypatch, [429, 502, 429])

    assert client.request("get", "url") == "ok"
    assert sleeps == [10, 5, 20]


@pytest.mark.parametrize("method", ["post", "put", "delete"])
@pytest.mark.parametrize("failure", [
    writer.APIError(_FakeHTMLResponse(502)),
    writer.requests.exceptions.ChunkedEncodingError("Connection broken"),
])
def test_write_is_not_retried_after_a_transient_failure(monkeypatch, method, failure):
    client, calls, sleeps = _quota_client(monkeypatch, [failure])

    with pytest.raises(type(failure)):
        client.request(method, "url")
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("code", [400, 403, 404])
def test_read_is_not_retried_after_a_client_error(monkeypatch, code):
    client, calls, sleeps = _quota_client(monkeypatch, [code])

    with pytest.raises(writer.APIError):
        client.request("get", "url")
    assert len(calls) == 1
    assert sleeps == []


def test_append_jobs_deduplicates_incoming_batch():
    worksheet = _FakeWorksheet()
    job = _job('Acme', '123')
    assert writer.append_jobs(worksheet, [job, job]) == [job]
    assert worksheet.appended == [job.to_row()]


def test_append_jobs_rejects_invalid_key_before_write():
    worksheet = _FakeWorksheet()
    with pytest.raises(ValueError, match='data quality'):
        writer.append_jobs(worksheet, [_job('Acme', ' 123')])
    assert worksheet.appended is None


@pytest.mark.parametrize("action", ["append", "prune"])
def test_blank_tab_is_rejected_before_writing(action):
    worksheet = _FakeWorksheet([])
    with pytest.raises(RuntimeError, match="refusing to append or prune"):
        if action == "append":
            writer.append_jobs(worksheet, [_job("Acme", "1")])
        else:
            writer.plan_prune(worksheet, [], {"Acme"})
    assert worksheet.appended is None
    assert worksheet.client.batch_update_calls == []


def test_filter_range_update_preserves_criteria_and_sort(monkeypatch):
    worksheet = _FakeWorksheet()
    original = {
        "range": {"sheetId": worksheet.id, "endColumnIndex": 3},
        "criteria": {"2": {"hiddenValues": ["Acme"]}},
        "sortSpecs": [{"dimensionIndex": 0, "sortOrder": "DESCENDING"}],
    }
    monkeypatch.setattr(worksheet.client, "fetch_sheet_metadata", lambda *a, **kw: {
        "sheets": [{"properties": {"sheetId": worksheet.id}, "basicFilter": original}]
    })
    writer.set_basic_filter(worksheet)
    written = worksheet.client.batch_update_calls[0][1]["requests"][0]["setBasicFilter"]["filter"]
    assert written["criteria"] == original["criteria"]
    assert written["sortSpecs"] == original["sortSpecs"]
    assert written["range"] == _WHOLE_TAB
    assert original["range"]["endColumnIndex"] == 3
