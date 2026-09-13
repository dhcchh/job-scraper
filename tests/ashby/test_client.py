"""Tests for ashby.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/lever/test_client.py's structure, adapted to Ashby's response
shape: {"jobs": [...]} (like Greenhouse, not a bare array like Lever),
`title`/`jobUrl` (not `text`/`hostedUrl`), and a primary `location` string
plus `secondaryLocations[].location` instead of one combined
`categories.allLocations` list.
"""

import datetime

import pytest

from jobscraper.job_boards.ashby import client

_SAMPLE_RESPONSE = {
    "jobs": [
        {
            "id": "1fc309c8-da20-4ff2-84c7-8b863ece2b0a",
            "title": "Senior Backend Engineer",
            "department": "Engineering",
            "team": "Platform",
            "location": "London",
            "secondaryLocations": [],
            "publishedAt": "2025-06-15T09:00:00.000+00:00",
            "jobUrl": "https://jobs.ashbyhq.com/acme/1fc309c8",
            "applyUrl": "https://jobs.ashbyhq.com/acme/1fc309c8/application",
        },
        {
            "id": "10dfc8bc-99ad-4ca2-ab76-853cb90a92c2",
            "title": "No department or location",
            "department": "",
            "team": "",
            "location": "",
            "secondaryLocations": [],
            "publishedAt": "",
            "jobUrl": "https://jobs.ashbyhq.com/acme/10dfc8bc",
        },
    ],
    "apiVersion": "1",
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

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_jobs(
        "acme", "Acme Corp", "Big Tech", scraped_date=datetime.date(2026, 9, 12)
    )

    assert captured["url"] == f"{client.BASE_URL}/acme"
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Acme Corp"  # firm name, not the "acme" board slug
    assert first.job_id == "1fc309c8-da20-4ff2-84c7-8b863ece2b0a"
    assert first.department == "Engineering"
    assert first.location == "London, United Kingdom"
    assert first.url == "https://jobs.ashbyhq.com/acme/1fc309c8"
    assert first.scraped_date == datetime.date(2026, 9, 12)
    assert first.seniority == "Senior"  # from "Senior Backend Engineer"
    assert first.company_type == "Big Tech"
    assert first.first_published == "2025-06-15"
    assert first.updated_at == ""  # Ashby's API has no edited-at timestamp at all

    # Missing department/location/publishedAt degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""


def test_fetch_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
    response = {
        "jobs": [
            {
                "title": "No id field",
                "jobUrl": "https://x/bad",
                "secondaryLocations": [],
            },
            {
                "id": "real-id",
                "title": "Software Engineer",
                "jobUrl": "https://x/real-id",
                "secondaryLocations": [],
            },
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    jobs = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "real-id"


def test_fetch_jobs_builds_location_from_primary_and_secondary_and_dedupes(
    monkeypatch,
):
    response = {
        "jobs": [
            {
                "id": "1",
                "title": "Software Engineer",
                "jobUrl": "https://x/1",
                "location": "London",
                "secondaryLocations": [
                    {"location": "New York City"},
                    {"location": "NYC"},  # normalizes to the same city as
                ],  # the entry above -- must be deduplicated
            }
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "London, United Kingdom | New York, NY"


def test_fetch_jobs_falls_back_to_team_when_department_is_missing(monkeypatch):
    response = {
        "jobs": [
            {
                "id": "1",
                "title": "Administrative Business Partner",
                "jobUrl": "https://x/1",
                "team": "Administrative",
                "secondaryLocations": [],
            }
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert job.department == "Administrative"


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

    def fake_get(url, timeout=None):
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

    def fake_get(url, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(calls) == client._MAX_ATTEMPTS


def test_fetch_jobs_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _RespondingWith(404)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(
        client.time, "sleep", lambda seconds: pytest.fail("should not retry a 404")
    )

    with pytest.raises(client.requests.HTTPError):
        client.fetch_jobs("acme", "Acme Corp", "Big Tech")

    assert len(calls) == 1  # no retries -- a 404 won't fix itself
