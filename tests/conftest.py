"""Isolate tests from local credentials and block unmocked HTTP requests."""

import os

import pytest
import requests

os.environ.update({
    "PYTHON_DOTENV_DISABLED": "1",
    "GOOGLE_SHEET_ID": "test-sheet-id",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
    "GOOGLE_SHEET_TITLE": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "TELEGRAM_THREAD_ID": "",
    "TELEGRAM_COMPANIES_CHAT_ID": "",
    "TELEGRAM_COMPANIES_THREAD_ID": "",
    "TELEGRAM_DM_CHAT_ID": "",
    "ONLY_FIRMS": "",
    "FORCE_PRUNE_FIRMS": "",
})


@pytest.fixture(autouse=True)
def block_unmocked_http(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Test attempted an unmocked HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", fail)
