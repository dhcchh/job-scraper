"""Tests for main.run()'s per-board error handling.

fetch_jobs, get_spreadsheet, get_worksheet, append_jobs, prune_closed_postings
and sort_rows are all monkeypatched here -- this exercises only run()'s own
orchestration (which boards it treats as "fetched" for pruning purposes),
not gspread or the network.
"""

import datetime

import pytest

from jobscraper import main
from jobscraper.models import Job


def _job(firm, job_id, title="Software Engineer"):
    return Job(firm, job_id, title, "Eng", "Remote", "https://x", "", datetime.date(2026, 9, 12))


def _patch_sheet_functions(monkeypatch, prune_calls, append_calls=None):
    monkeypatch.setattr(main, "get_spreadsheet", lambda: "SPREADSHEET")
    monkeypatch.setattr(main, "get_worksheet", lambda spreadsheet, name: name)
    monkeypatch.setattr(main, "sort_rows", lambda worksheet: None)
    monkeypatch.setattr(
        main,
        "append_jobs",
        lambda worksheet, jobs: append_calls.append((worksheet, jobs))
        if append_calls is not None
        else None,
    )
    monkeypatch.setattr(
        main,
        "prune_closed_postings",
        lambda worksheet, jobs, fetched_boards: prune_calls.append(fetched_boards),
    )


def test_run_skips_failed_board_and_prunes_only_succeeded_firms(monkeypatch):
    boards = [
        {"token": "good", "firm": "GoodCo", "company_type": "Big Tech"},
        {"token": "bad", "firm": "BadCo", "company_type": "Big Tech"},
    ]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    good_job = _job("GoodCo", "1")

    def fake_fetch_jobs(token, firm, company_type):
        if firm == "BadCo":
            raise RuntimeError("greenhouse down")
        return [good_job]

    monkeypatch.setattr(main, "fetch_jobs", fake_fetch_jobs)

    prune_calls: list[set[str]] = []
    append_calls: list[tuple] = []
    _patch_sheet_functions(monkeypatch, prune_calls, append_calls)

    main.run()  # must not raise -- BadCo's failure shouldn't abort the run

    assert prune_calls  # ran for every category
    assert all(fetched_boards == {"GoodCo"} for fetched_boards in prune_calls)

    swe_jobs = next(jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE")
    assert swe_jobs == [good_job]


def test_run_excludes_firm_from_prune_if_any_of_its_boards_failed(monkeypatch):
    """Optiver has two board tokens under one firm -- if only one fails,
    the firm as a whole must still be excluded from fetched_boards, or the
    failed token's still-open postings would look closed and get pruned."""
    boards = [
        {"token": "optiver1", "firm": "Optiver", "company_type": "Prop Trading/Hedge Fund"},
        {"token": "optiver2", "firm": "Optiver", "company_type": "Prop Trading/Hedge Fund"},
    ]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_jobs(token, firm, company_type):
        if token == "optiver2":
            raise RuntimeError("greenhouse down")
        return [_job("Optiver", "1")]

    monkeypatch.setattr(main, "fetch_jobs", fake_fetch_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    main.run()

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)


def test_run_completes_when_every_board_fails(monkeypatch):
    boards = [{"token": "bad", "firm": "BadCo", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_jobs(token, firm, company_type):
        raise RuntimeError("greenhouse down")

    monkeypatch.setattr(main, "fetch_jobs", fake_fetch_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    main.run()  # no board succeeded, but run() still completes

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)
