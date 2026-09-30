import os
import subprocess
import sys

from jobscraper import board_health, config


def test_board_health_imports_without_sheet_credentials():
    env = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
    env.pop("GOOGLE_SHEET_ID", None)
    env.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
    result = subprocess.run([
        sys.executable, "-c",
        "from jobscraper import board_health, config; "
        "assert config.GOOGLE_SHEET_ID == ''; "
        "assert config.GOOGLE_SERVICE_ACCOUNT_JSON == ''",
    ], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_health_checks_use_provider_options_without_a_sheet(monkeypatch):
    board = {"firm": "Acme", "token": "acme.wd1/Jobs", "company_type": "Tech", "location_scope": "Singapore"}
    calls = []
    monkeypatch.setattr(config, "GOOGLE_SHEET_ID", "")
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setattr(board_health.fetchers, "board_sources", lambda: [
        ("workday", [board], lambda *a, **kw: calls.append((a, kw)) or [])
    ])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert board_health.check()
    assert calls == [(("acme.wd1/Jobs", "Acme", "Tech"), {"location_scope": "Singapore"})]


def test_unknown_firm_filter_fails_instead_of_reporting_success(monkeypatch):
    monkeypatch.setattr(config, "ONLY_FIRMS", frozenset({"not-configured"}))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert not board_health.check()
