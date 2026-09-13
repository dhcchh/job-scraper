"""Tests for main.run()'s per-board error handling, startup spreadsheet-id
logging, and end-of-run job summary.

fetch_greenhouse_jobs/fetch_lever_jobs/fetch_ashby_jobs, get_spreadsheet,
get_worksheet, append_jobs and
prune_closed_postings are all monkeypatched here -- this exercises only
run()'s own orchestration (which boards it treats as "fetched" for pruning
purposes), not gspread or the network. GITHUB_STEP_SUMMARY is explicitly
unset in _patch_sheet_functions so these orchestration tests can't
accidentally write into a real one if pytest is ever run inside a GitHub
Actions job itself; the summary-writing tests below set it to a tmp_path
file instead.
"""

import datetime

import pytest

from jobscraper import main
from jobscraper.models import Job


def _job(firm, job_id, title="Software Engineer", location="Remote"):
    return Job(firm, job_id, title, "Eng", location, "https://x", "", datetime.date(2026, 9, 12))


class _FakeSpreadsheet:
    title = "Test Sheet"
    url = "https://docs.google.com/spreadsheets/d/test-sheet-id"


def _patch_sheet_functions(monkeypatch, prune_calls, append_calls=None):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(main, "get_spreadsheet", lambda: _FakeSpreadsheet())
    monkeypatch.setattr(main, "get_worksheet", lambda spreadsheet, name: name)
    # Empty by default so a test that only cares about Greenhouse behavior
    # doesn't also fetch the real, committed company_toml/lever_boards.toml
    # / company_toml/ashby_boards.toml over the network -- a test exercising
    # the Lever/Ashby loop overrides these itself.
    monkeypatch.setattr(main.config, "LEVER_BOARDS", [])
    monkeypatch.setattr(main.config, "ASHBY_BOARDS", [])

    def fake_append_jobs(worksheet, jobs):
        if append_calls is not None:
            append_calls.append((worksheet, jobs))
        return len(jobs)

    def fake_prune_closed_postings(worksheet, jobs, fetched_boards):
        prune_calls.append(fetched_boards)
        return 0

    monkeypatch.setattr(main, "append_jobs", fake_append_jobs)
    monkeypatch.setattr(main, "prune_closed_postings", fake_prune_closed_postings)


def test_run_fails_after_updating_succeeded_boards(monkeypatch):
    boards = [
        {"token": "good", "firm": "GoodCo", "company_type": "Big Tech"},
        {"token": "bad", "firm": "BadCo", "company_type": "Big Tech"},
    ]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    good_job = _job("GoodCo", "1")

    def fake_fetch_greenhouse_jobs(token, firm, company_type):
        if firm == "BadCo":
            raise RuntimeError("greenhouse down")
        return [good_job]

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", fake_fetch_greenhouse_jobs)

    prune_calls: list[set[str]] = []
    append_calls: list[tuple] = []
    _patch_sheet_functions(monkeypatch, prune_calls, append_calls)

    with pytest.raises(RuntimeError, match="BadCo"):
        main.run()

    assert prune_calls  # ran for every category
    assert all(fetched_boards == {"GoodCo"} for fetched_boards in prune_calls)

    # good_job's location ("Remote") isn't a PAC country -- lands in RoW.
    swe_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - RoW"
    )
    assert swe_jobs == [good_job]


def test_run_routes_jobs_to_category_and_region_tabs(monkeypatch):
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    pac_job = _job("Acme", "1", location="Singapore")
    row_job = _job("Acme", "2", location="London, United Kingdom")

    monkeypatch.setattr(
        main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [pac_job, row_job]
    )

    prune_calls: list[set[str]] = []
    append_calls: list[tuple] = []
    _patch_sheet_functions(monkeypatch, prune_calls, append_calls)

    main.run()

    pac_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - PAC"
    )
    row_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - RoW"
    )
    assert pac_jobs == [pac_job]
    assert row_jobs == [row_job]


def test_run_fetches_and_merges_lever_boards_alongside_greenhouse(monkeypatch):
    monkeypatch.setattr(
        main.config,
        "GREENHOUSE_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}],
    )

    gh_job = _job("Acme", "1", location="Singapore")
    lever_job = _job("Palantir", "2", location="London, United Kingdom")

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [gh_job])
    monkeypatch.setattr(
        main, "fetch_lever_jobs", lambda token, firm, company_type: [lever_job]
    )

    prune_calls: list[set[str]] = []
    append_calls: list[tuple] = []
    _patch_sheet_functions(monkeypatch, prune_calls, append_calls)
    # Override _patch_sheet_functions' empty-by-default LEVER_BOARDS -- this
    # test specifically exercises the Lever loop.
    monkeypatch.setattr(
        main.config,
        "LEVER_BOARDS",
        [{"token": "palantir", "firm": "Palantir", "company_type": "Data/Cloud Infrastructure"}],
    )

    main.run()

    assert all(fetched_boards == {"Acme", "Palantir"} for fetched_boards in prune_calls)
    pac_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - PAC"
    )
    row_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - RoW"
    )
    assert pac_jobs == [gh_job]
    assert row_jobs == [lever_job]


def test_run_fetches_and_merges_ashby_boards_alongside_greenhouse(monkeypatch):
    monkeypatch.setattr(
        main.config,
        "GREENHOUSE_BOARDS",
        [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}],
    )

    gh_job = _job("Acme", "1", location="Singapore")
    ashby_job = _job("OpenAI", "2", location="London, United Kingdom")

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [gh_job])
    monkeypatch.setattr(
        main, "fetch_ashby_jobs", lambda token, firm, company_type: [ashby_job]
    )

    prune_calls: list[set[str]] = []
    append_calls: list[tuple] = []
    _patch_sheet_functions(monkeypatch, prune_calls, append_calls)
    # Override _patch_sheet_functions' empty-by-default ASHBY_BOARDS -- this
    # test specifically exercises the Ashby loop.
    monkeypatch.setattr(
        main.config,
        "ASHBY_BOARDS",
        [{"token": "openai", "firm": "OpenAI", "company_type": "AI Lab"}],
    )

    main.run()

    assert all(fetched_boards == {"Acme", "OpenAI"} for fetched_boards in prune_calls)
    pac_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - PAC"
    )
    row_jobs = next(
        jobs for worksheet, jobs in append_calls if worksheet == "SWE/DE - RoW"
    )
    assert pac_jobs == [gh_job]
    assert row_jobs == [ashby_job]


def test_run_treats_a_failed_ashby_board_like_a_failed_greenhouse_board(monkeypatch):
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", [])

    def fake_fetch_ashby_jobs(token, firm, company_type):
        raise RuntimeError("ashby down")

    monkeypatch.setattr(main, "fetch_ashby_jobs", fake_fetch_ashby_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)
    # Override _patch_sheet_functions' empty-by-default ASHBY_BOARDS -- this
    # test specifically exercises the Ashby loop's failure handling.
    monkeypatch.setattr(
        main.config,
        "ASHBY_BOARDS",
        [{"token": "bad", "firm": "BadCo", "company_type": "Big Tech"}],
    )

    with pytest.raises(RuntimeError, match="BadCo"):
        main.run()

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)


def test_run_treats_a_failed_lever_board_like_a_failed_greenhouse_board(monkeypatch):
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", [])

    def fake_fetch_lever_jobs(token, firm, company_type):
        raise RuntimeError("lever down")

    monkeypatch.setattr(main, "fetch_lever_jobs", fake_fetch_lever_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)
    # Override _patch_sheet_functions' empty-by-default LEVER_BOARDS -- this
    # test specifically exercises the Lever loop's failure handling.
    monkeypatch.setattr(
        main.config,
        "LEVER_BOARDS",
        [{"token": "bad", "firm": "BadCo", "company_type": "Big Tech"}],
    )

    with pytest.raises(RuntimeError, match="BadCo"):
        main.run()

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)


def test_run_excludes_firm_from_prune_if_any_of_its_boards_failed(monkeypatch):
    """Optiver has two board tokens under one firm -- if only one fails,
    the firm as a whole must still be excluded from fetched_boards, or the
    failed token's still-open postings would look closed and get pruned."""
    boards = [
        {"token": "optiver1", "firm": "Optiver", "company_type": "Prop Trading/Hedge Fund"},
        {"token": "optiver2", "firm": "Optiver", "company_type": "Prop Trading/Hedge Fund"},
    ]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_greenhouse_jobs(token, firm, company_type):
        if token == "optiver2":
            raise RuntimeError("greenhouse down")
        return [_job("Optiver", "1")]

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", fake_fetch_greenhouse_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    with pytest.raises(RuntimeError, match="Optiver"):
        main.run()

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)


def test_run_completes_when_every_board_fails(monkeypatch):
    boards = [{"token": "bad", "firm": "BadCo", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_greenhouse_jobs(token, firm, company_type):
        raise RuntimeError("greenhouse down")

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", fake_fetch_greenhouse_jobs)

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    with pytest.raises(RuntimeError, match="BadCo"):
        main.run()

    assert prune_calls
    assert all(fetched_boards == set() for fetched_boards in prune_calls)


def test_run_logs_spreadsheet_identity_at_startup(monkeypatch, caplog):
    # Guards against a GOOGLE_SHEET_ID that's drifted between .env and the
    # CI secret -- the log line is the only way such a mismatch becomes
    # visible.
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)
    monkeypatch.setattr(main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [])

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    with caplog.at_level("INFO"):
        main.run()

    assert _FakeSpreadsheet.title in caplog.text
    assert _FakeSpreadsheet.url in caplog.text


def test_run_writes_job_summary_when_github_step_summary_is_set(monkeypatch, tmp_path):
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)
    job = _job("Acme", "1", location="Singapore")
    monkeypatch.setattr(main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [job])

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

    main.run()

    content = summary_path.read_text()
    assert "Job scraper run: success" in content
    assert _FakeSpreadsheet.title in content
    assert "Boards fetched: 1/1" in content
    assert "SWE/DE - PAC" in content  # job's Singapore location -> PAC tab


def test_run_writes_partial_summary_before_failing(monkeypatch, tmp_path):
    boards = [
        {"token": "good", "firm": "GoodCo", "company_type": "Big Tech"},
        {"token": "bad", "firm": "BadCo", "company_type": "Big Tech"},
    ]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)

    def fake_fetch_greenhouse_jobs(token, firm, company_type):
        if token == "bad":
            raise RuntimeError("greenhouse down")
        return [_job("GoodCo", "1", location="Singapore")]

    monkeypatch.setattr(main, "fetch_greenhouse_jobs", fake_fetch_greenhouse_jobs)
    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

    with pytest.raises(RuntimeError, match="BadCo"):
        main.run()

    content = summary_path.read_text()
    assert "Job scraper run: partial failure" in content
    assert "Boards fetched: 1/2 -- failed: BadCo" in content


def test_run_skips_job_summary_without_github_step_summary(monkeypatch):
    boards = [{"token": "acme", "firm": "Acme", "company_type": "Big Tech"}]
    monkeypatch.setattr(main.config, "GREENHOUSE_BOARDS", boards)
    monkeypatch.setattr(main, "fetch_greenhouse_jobs", lambda token, firm, company_type: [])

    prune_calls: list[set[str]] = []
    _patch_sheet_functions(monkeypatch, prune_calls)

    main.run()  # GITHUB_STEP_SUMMARY unset -- must not raise or write anywhere


def test_build_summary_markdown_reports_failure_and_per_tab_counts():
    markdown = main._build_summary_markdown(
        spreadsheet_title="Test Sheet",
        spreadsheet_url="https://example.com/sheet",
        succeeded_firms={"GoodCo"},
        failed_firms={"BadCo"},
        total_boards=2,
        total_jobs=10,
        noise_count=3,
        tab_stats={"SWE/DE - PAC": (2, 1, 0), "SWE/DE - RoW": (5, 4, 1)},
    )

    assert "## Job scraper run: partial failure" in markdown
    assert "[Test Sheet](https://example.com/sheet)" in markdown
    assert "Boards fetched: 1/2 -- failed: BadCo" in markdown
    assert "Jobs fetched: 10 (3 filtered as noise)" in markdown
    assert "Appended: 5 new row(s)" in markdown
    assert "Pruned: 1 closed posting(s)" in markdown
    assert "| SWE/DE - PAC | 2 | 1 | 0 |" in markdown
    assert "| SWE/DE - RoW | 5 | 4 | 1 |" in markdown


def test_build_summary_markdown_reports_success_when_nothing_failed():
    markdown = main._build_summary_markdown(
        spreadsheet_title="Test Sheet",
        spreadsheet_url="https://example.com/sheet",
        succeeded_firms={"GoodCo"},
        failed_firms=set(),
        total_boards=1,
        total_jobs=1,
        noise_count=0,
        tab_stats={},
    )

    assert "## Job scraper run: success" in markdown
    assert "Boards fetched: 1/1" in markdown
    assert "-- failed" not in markdown


def test_write_job_summary_appends_to_existing_file(monkeypatch, tmp_path):
    path = tmp_path / "summary.md"
    path.write_text("existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))

    main._write_job_summary("new content\n")

    assert path.read_text() == "existing\nnew content\n"


def test_write_job_summary_noop_without_env_var(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    main._write_job_summary("content")  # must not raise
