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
        self.cleared = False
        self.updated_with = None

    def get_all_values(self):
        return self._rows

    def append_rows(self, rows, value_input_option=None):
        self.appended = rows

    def clear(self):
        self.cleared = True

    def update(self, values, value_input_option=None):
        self.updated_with = values


def _row(board, job_id, scraped_date):
    return [board, job_id, "Title", "Dept", "Remote", "https://x", "", scraped_date]


def test_append_jobs_writes_job_rows():
    worksheet = _FakeWorksheet()
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    writer.append_jobs(worksheet, [job])

    assert worksheet.appended == [job.to_row()]


def test_append_jobs_noop_on_empty_list():
    worksheet = _FakeWorksheet()
    writer.append_jobs(worksheet, [])
    assert worksheet.appended is None


def test_append_jobs_skips_already_tracked_job():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    writer.append_jobs(worksheet, [job])

    assert worksheet.appended is None  # already tracked -- not re-appended


def test_append_jobs_appends_only_the_new_ones():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    seen = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))
    new = Job("acme", "2", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    writer.append_jobs(worksheet, [seen, new])

    assert worksheet.appended == [new.to_row()]


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

    writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.cleared is True
    kept_ids = [row[1] for row in worksheet.updated_with[1:]]
    assert kept_ids == ["2", "3"]


def test_prune_noop_when_nothing_closed():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-12")]
    worksheet = _FakeWorksheet(rows)
    current_jobs = [_job("acme", "1")]

    writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.cleared is False  # nothing closed, no rewrite needed


def test_prune_leaves_unfetched_board_rows_alone():
    # "beta" wasn't fetched this run (failed, or removed from config) --
    # its rows must survive even though beta has no entries in current_jobs.
    rows = [writer.HEADER, _row("beta", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards={"acme"})

    assert worksheet.cleared is False


def test_prune_a_board_with_zero_current_postings_removes_all_its_rows():
    # acme fetched successfully but currently has no open postings at all --
    # every existing acme row should be treated as closed.
    rows = [writer.HEADER, _row("acme", "1", "2026-01-01")]
    worksheet = _FakeWorksheet(rows)

    writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards={"acme"})

    assert worksheet.cleared is True
    assert worksheet.updated_with == [writer.HEADER]


def test_prune_missing_board_column_raises():
    worksheet = _FakeWorksheet([["job_id"]])
    with pytest.raises(ValueError):
        writer.prune_closed_postings(worksheet, current_jobs=[], fetched_boards=set())


def test_row_key_tolerates_malformed_row():
    assert writer._row_key(["a"], board_index=0, job_id_index=5) is None


def _row_with_location(board, job_id, scraped_date, location):
    return [board, job_id, "Title", "Dept", location, "https://x", "", scraped_date]


def test_sort_rows_orders_by_recency_then_location_then_board():
    rows = [
        writer.HEADER,
        _row_with_location("beta", "1", "2026-09-10", "NYC"),
        _row_with_location("acme", "2", "2026-09-12", "London"),
        _row_with_location("acme", "3", "2026-09-12", "Chicago"),
        _row_with_location("zeta", "4", "2026-09-12", "Chicago"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.sort_rows(worksheet)

    assert worksheet.updated_with is not None
    ids_in_order = [row[1] for row in worksheet.updated_with[1:]]
    # 2026-09-12 (recent) before 2026-09-10; within 2026-09-12, Chicago before
    # London; within Chicago, "acme" before "zeta".
    assert ids_in_order == ["3", "4", "2", "1"]


def test_sort_rows_location_comparison_is_case_insensitive():
    # Plain string comparison would put "APAC" (all-caps) before "Aarhus",
    # since uppercase letters sort before lowercase in ASCII -- not what a
    # human reading the sheet would expect.
    rows = [
        writer.HEADER,
        _row_with_location("acme", "1", "2026-09-12", "APAC"),
        _row_with_location("acme", "2", "2026-09-12", "Aarhus"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.sort_rows(worksheet)

    ids_in_order = [row[1] for row in worksheet.updated_with[1:]]
    assert ids_in_order == ["2", "1"]


def test_sort_rows_location_comparison_ignores_stray_whitespace():
    # Greenhouse's own multi-location strings sometimes carry a stray
    # leading space (e.g. " London, Montreal, Singapore"). Unstripped, a
    # space sorts before every letter and would wrongly put this row first.
    rows = [
        writer.HEADER,
        _row_with_location("acme", "1", "2026-09-12", " London, Montreal"),
        _row_with_location("acme", "2", "2026-09-12", "Aarhus"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.sort_rows(worksheet)

    ids_in_order = [row[1] for row in worksheet.updated_with[1:]]
    assert ids_in_order == ["2", "1"]


def _row_full(board, job_id, scraped_date, location, company_type):
    return [
        board,
        job_id,
        "Title",
        "Dept",
        location,
        "https://x",
        "",
        scraped_date,
        "Unspecified",
        company_type,
    ]


def test_sort_rows_orders_by_company_type_before_board():
    rows = [
        writer.HEADER,
        _row_full("Zeta Corp", "1", "2026-09-12", "NYC", "Startup"),
        _row_full("Acme Fund", "2", "2026-09-12", "NYC", "Hedge Fund"),
        _row_full("Beta Fund", "3", "2026-09-12", "NYC", "Hedge Fund"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.sort_rows(worksheet)

    ids_in_order = [row[1] for row in worksheet.updated_with[1:]]
    # Same date & location for all three -- "Hedge Fund" sorts before
    # "Startup"; within Hedge Fund, "Acme Fund" before "Beta Fund".
    assert ids_in_order == ["2", "3", "1"]


def test_sort_rows_noop_when_already_sorted():
    rows = [
        writer.HEADER,
        _row_with_location("acme", "1", "2026-09-12", "Chicago"),
        _row_with_location("beta", "2", "2026-09-10", "NYC"),
    ]
    worksheet = _FakeWorksheet(rows)

    writer.sort_rows(worksheet)

    assert worksheet.updated_with is None  # already in order, no rewrite


def test_sort_rows_noop_on_empty_sheet():
    worksheet = _FakeWorksheet([])
    writer.sort_rows(worksheet)
    assert worksheet.updated_with is None


def test_safe_value_tolerates_malformed_row():
    assert writer._safe_value(["a"], 5) == ""
