"""Tests for concurrent board fetching using fake fetch functions."""

import threading
import time

from jobscraper.job_boards import fetchers


def _board(firm, **extra):
    return {"firm": firm, "token": firm.lower(), "company_type": "Tech", **extra}


def test_fetch_boards_keeps_configuration_order_when_boards_finish_out_of_order():
    def fetch(token, firm, company_type):
        # Earlier boards finish last.
        time.sleep({"A": 0.05, "B": 0.02, "C": 0.0}[firm])
        return [firm]

    results = fetchers.fetch_boards([
        ("greenhouse", [_board("A"), _board("B")], fetch),
        ("lever", [_board("C")], fetch),
    ])

    assert [(r.source, r.board["firm"], r.jobs) for r in results] == [
        ("greenhouse", "A", ["A"]),
        ("greenhouse", "B", ["B"]),
        ("lever", "C", ["C"]),
    ]


def test_fetch_boards_runs_boards_concurrently():
    # Both fetches must be in flight at once for either to pass the barrier.
    barrier = threading.Barrier(2, timeout=5)

    def fetch(token, firm, company_type):
        barrier.wait()
        return []

    results = fetchers.fetch_boards([("greenhouse", [_board("A"), _board("B")], fetch)])

    assert all(r.error is None for r in results)


def test_fetch_boards_reports_each_failure_without_failing_other_boards():
    error = RuntimeError("boom")

    def fetch(token, firm, company_type):
        if firm == "Bad":
            raise error
        return ["job"]

    results = fetchers.fetch_boards([("ashby", [_board("Bad"), _board("Good")], fetch)])

    assert [(r.board["firm"], r.jobs, r.error) for r in results] == [
        ("Bad", [], error),
        ("Good", ["job"], None),
    ]


def test_fetch_boards_passes_workday_location_scope():
    calls = []

    def fetch(token, firm, company_type, **kwargs):
        calls.append(kwargs)
        return []

    fetchers.fetch_boards([("workday", [_board("W", location_scope="Singapore")], fetch)])

    assert calls == [{"location_scope": "Singapore"}]
