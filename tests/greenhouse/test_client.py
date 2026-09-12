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
            "first_published": "2026-06-15T08:00:00-04:00",
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

    jobs = client.fetch_jobs(
        "acme", "Acme Corp", "Big Tech", scraped_date=datetime.date(2026, 9, 12)
    )

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
    assert first.company_type == "Big Tech"
    assert first.first_published == "2026-06-15T08:00:00-04:00"

    # Missing department/location degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""  # missing from the raw job, not a crash


def test_fetch_jobs_builds_location_from_offices_not_the_raw_location_string(monkeypatch):
    # location.name is one free-text string a recruiter typed and can't be
    # safely split ("London, United Kingdom; New York, NY, United States");
    # offices is Greenhouse's own pre-split, per-office list -- that's what
    # should drive Job.location, normalized and " | "-joined.
    response = {
        "jobs": [
            {
                "id": 1,
                "title": "Software Engineer",
                "updated_at": "",
                "absolute_url": "https://x/1",
                "location": {"name": "London, United Kingdom; New York, NY, United States"},
                "departments": [],
                "offices": [
                    {"name": "London"},
                    {"name": "New York City"},  # normalizes to the same
                    {"name": "NYC"},            # city as the entry above --
                ],                              # must be de-duplicated
            }
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "London, United Kingdom | New York, NY"


def test_fetch_jobs_falls_back_to_location_name_when_offices_missing(monkeypatch):
    response = {
        "jobs": [
            {
                "id": 1,
                "title": "Software Engineer",
                "updated_at": "",
                "absolute_url": "https://x/1",
                "location": {"name": "Bangalore"},
                "departments": [],
                "offices": [],
            }
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "Bangalore, India"  # still normalized via the fallback


def test_fetch_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp", "Big Tech")


class _RespondingWith:
    """A fake response that raises an HTTPError carrying the given status."""

    def __init__(self, status_code):
        self._status_code = status_code

    @property
    def status_code(self):
        return self._status_code

    def raise_for_status(self):
        error = client.requests.HTTPError(f"{self._status_code} error")
        error.response = self
        raise error


def test_fetch_jobs_retries_transient_error_then_succeeds(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise client.requests.exceptions.ConnectionError("network blip")
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    jobs = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(calls) == 3  # 2 failures + 1 success, within _MAX_ATTEMPTS
    assert len(jobs) == 2


def test_fetch_jobs_gives_up_after_max_retries_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(calls) == client._MAX_ATTEMPTS


def test_fetch_jobs_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(404)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(
        client.time, "sleep", lambda seconds: pytest.fail("should not retry a 404")
    )

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(calls) == 1  # no retries -- a 404 won't fix itself
