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


def _flaky_fetch(failures_by_firm):
    """A fetch that fails each firm's first N calls, then returns [firm]."""
    calls = []

    def fetch(token, firm, company_type):
        calls.append(firm)
        if calls.count(firm) <= failures_by_firm.get(firm, 0):
            raise RuntimeError(f"{firm} glitch {calls.count(firm)}")
        return [firm]

    return fetch, calls


def test_fetch_boards_retries_a_failed_board_once_after_the_delay(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fetchers.time, "sleep", sleeps.append)
    fetch, calls = _flaky_fetch({"Flaky": 1})

    results = fetchers.fetch_boards(
        [("eightfold", [_board("Flaky"), _board("Good")], fetch)], retry_failed_after=60
    )

    assert [(r.board["firm"], r.jobs, r.error) for r in results] == [
        ("Flaky", ["Flaky"], None),
        ("Good", ["Good"], None),
    ]
    assert sorted(calls) == ["Flaky", "Flaky", "Good"]
    assert sleeps == [60]


def test_fetch_boards_reports_the_retry_error_when_a_board_fails_twice(monkeypatch):
    monkeypatch.setattr(fetchers.time, "sleep", lambda seconds: None)
    fetch, calls = _flaky_fetch({"Bad": 5})

    [result] = fetchers.fetch_boards([("ashby", [_board("Bad")], fetch)], retry_failed_after=60)

    assert calls == ["Bad", "Bad"]
    assert str(result.error) == "Bad glitch 2"


def test_fetch_boards_does_not_retry_or_wait_unless_asked_and_needed(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fetchers.time, "sleep", sleeps.append)
    fetch, calls = _flaky_fetch({"Bad": 1})

    fetchers.fetch_boards([("ashby", [_board("Bad")], fetch)])
    fetchers.fetch_boards([("ashby", [_board("Good")], fetch)], retry_failed_after=60)

    assert calls == ["Bad", "Good"]
    assert sleeps == []


def test_describe_error_is_one_bounded_line():
    assert fetchers.describe_error(ValueError("duplicate id:\n  42")) == "ValueError: duplicate id: 42"
    long = fetchers.describe_error(RuntimeError("x" * 500), limit=40)
    assert len(long) == 40 and long.endswith("…")
