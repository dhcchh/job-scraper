"""Tests for greenhouse.client -- JSON-to-Job parsing, no real HTTP calls."""

import datetime

import pytest

from jobscraper.job_boards.greenhouse import client

_SAMPLE_RESPONSE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Senior Backend Engineer",
            "updated_at": "2026-09-01T10:00:00-04:00",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
            "location": {"name": "Remote"},
            "departments": [{"id": 1, "name": "Engineering"}],
        },
        {
            "id": 67890,
            "title": "No department or location",
            "updated_at": "2026-09-02T10:00:00-04:00",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/67890",
            "location": {},
            "departments": [],
        },
    ]
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_jobs("acme", "Acme Corp", scraped_date=datetime.date(2026, 9, 12))

    assert captured["url"] == f"{client.BASE_URL}/acme/jobs"
    assert captured["params"] == {"content": "true"}
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Acme Corp"  # firm name, not the "acme" board token
    assert first.job_id == "12345"
    assert first.department == "Engineering"
    assert first.location == "Remote"
    assert first.scraped_date == datetime.date(2026, 9, 12)
    assert first.seniority == "Senior"  # from "Senior Backend Engineer"

    # Missing department/location degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"


def test_fetch_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp")
