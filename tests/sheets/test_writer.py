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
    }
    return [values[name] for name in writer.HEADER]


def test_append_jobs_writes_job_rows():
    worksheet = _FakeWorksheet()
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [job])

    assert worksheet.appended == [job.to_row()]
    assert appended == 1


def test_append_jobs_noop_on_empty_list():
    worksheet = _FakeWorksheet()
    appended = writer.append_jobs(worksheet, [])
    assert worksheet.appended is None
    assert appended == 0


def test_append_jobs_skips_already_tracked_job():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-01")]
    worksheet = _FakeWorksheet(rows)
    job = Job("acme", "1", "T", "D", "L", "U", "", datetime.date(2026, 9, 12))

    appended = writer.append_jobs(worksheet, [job])

    assert worksheet.appended is None  # already tracked -- not re-appended
    assert appended == 0


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

    pruned = writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.cleared is True
    job_id_index = writer.HEADER.index("job_id")
    kept_ids = [row[job_id_index] for row in worksheet.updated_with[1:]]
    assert kept_ids == ["2", "3"]
    assert pruned == 1


def test_prune_noop_when_nothing_closed():
    rows = [writer.HEADER, _row("acme", "1", "2026-09-12")]
    worksheet = _FakeWorksheet(rows)
    current_jobs = [_job("acme", "1")]

    pruned = writer.prune_closed_postings(worksheet, current_jobs, fetched_boards={"acme"})

    assert worksheet.cleared is False  # nothing closed, no rewrite needed
    assert pruned == 0


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


