"""Tests for static selection and optional live board validation."""

from types import SimpleNamespace

from jobscraper import validate_boards
from jobscraper.job_boards import fetchers


def _board(**overrides):
    return {
        "token": "acme",
        "firm": "Acme",
        "company_type": "Startup",
        **overrides,
    }


def _job(job_id="1", board="Acme"):
    return SimpleNamespace(job_id=job_id, board=board)


def test_select_boards_applies_provider_firm_and_token_filters():
    boards = {
        "greenhouse": [_board(), _board(token="beta", firm="Beta")],
        "lever": [_board(token="acme-lever")],
    }

    selected = validate_boards.select_boards(
        boards, providers=["greenhouse"], firms=["acme"], tokens=["acme"]
    )

    assert selected == [("greenhouse", _board())]


def test_validate_live_reports_a_success(monkeypatch):
    monkeypatch.setattr(
        fetchers,
        "fetch_greenhouse_jobs",
        lambda token, firm, company_type: [_job("1"), _job("2")],
    )
    output = []

    failures = validate_boards.validate_live(
        [("greenhouse", _board())], output=output.append
    )

    assert failures == 0
    assert output == [
        "OK   greenhouse[Acme] (acme): 2 job(s)",
        "Live validation: 1/1 board(s) passed",
    ]


def test_validate_live_passes_workday_location_scope(monkeypatch):
    received = {}

    def fetch(token, firm, company_type, **kwargs):
        received.update(kwargs)
        return [_job()]

    monkeypatch.setattr(fetchers, "fetch_workday_jobs", fetch)

    failures = validate_boards.validate_live(
        [("workday", _board(token="acme.wd5/jobs", location_scope="Singapore"))],
        output=lambda _message: None,
    )

    assert failures == 0
    assert received == {"location_scope": "Singapore"}


def test_validate_live_rejects_empty_board_by_default(monkeypatch):
    monkeypatch.setattr(
        fetchers,
        "fetch_greenhouse_jobs",
        lambda token, firm, company_type: [],
    )
    output = []

    failures = validate_boards.validate_live(
        [("greenhouse", _board())], output=output.append
    )

    assert failures == 1
    assert "returned no jobs" in output[0]
    assert "--allow-empty" in output[0]


def test_validate_live_can_allow_an_expected_empty_board(monkeypatch):
    monkeypatch.setattr(
        fetchers,
        "fetch_greenhouse_jobs",
        lambda token, firm, company_type: [],
    )

    failures = validate_boards.validate_live(
        [("greenhouse", _board())],
        allow_empty=True,
        output=lambda _message: None,
    )

    assert failures == 0


def test_validate_live_reports_endpoint_errors_and_continues(monkeypatch):
    def fail(token, firm, company_type):
        raise RuntimeError("not found")

    monkeypatch.setattr(fetchers, "fetch_greenhouse_jobs", fail)
    monkeypatch.setattr(
        fetchers,
        "fetch_lever_jobs",
        lambda token, firm, company_type: [_job()],
    )
    output = []

    failures = validate_boards.validate_live(
        [("greenhouse", _board()), ("lever", _board())], output=output.append
    )

    assert failures == 1
    assert output[0].endswith("RuntimeError: not found")
    assert output[1].startswith("OK   lever")
    assert output[2] == "Live validation: 1/2 board(s) passed"


def test_validate_live_routes_citadel_through_registered_fetcher(monkeypatch):
    monkeypatch.setattr(fetchers, "fetch_citadel_jobs", lambda *args: [_job("software-engineer", "Citadel")])
    board = _board(token="citadel", firm="Citadel", company_type="Prop Trading/Hedge Fund")
    assert validate_boards.validate_live([("citadel", board)], output=lambda _: None) == 0
