"""Tests for sheets.writer -- append/prune logic, no live gspread/Google call.

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


def test_prune_removes_rows_older_than_cutoff():
    rows = [
        writer.HEADER,
        _row("acme", "1", "2026-07-01"),  # older than 60 days before 2026-09-12
        _row("acme", "2", "2026-08-20"),  # within 60 days
        _row("acme", "3", "2026-09-12"),  # today
    ]
    worksheet = _FakeWorksheet(rows)

    writer.prune_old_rows(
        worksheet, older_than_days=60, today=datetime.date(2026, 9, 12)
    )

    assert worksheet.cleared is True
    kept_ids = [row[1] for row in worksheet.updated_with[1:]]
    assert kept_ids == ["2", "3"]


def test_prune_noop_when_nothing_is_stale():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-12")]
    worksheet = _FakeWorksheet(rows)

    writer.prune_old_rows(
        worksheet, older_than_days=60, today=datetime.date(2026, 9, 12)
    )

    assert worksheet.cleared is False  # nothing pruned, no rewrite needed


def test_prune_missing_scraped_date_column_raises():
    worksheet = _FakeWorksheet([["board", "job_id"]])
    with pytest.raises(ValueError):
        writer.prune_old_rows(worksheet)


def test_row_scraped_date_tolerates_malformed_row():
    assert writer._row_scraped_date(["a", "b"], 5) == datetime.date.min
    assert writer._row_scraped_date(["a", "not-a-date"], 1) == datetime.date.min
