"""Tests for ashby.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/lever/test_client.py's structure, adapted to Ashby's response
shape: {"jobs": [...]} (like Greenhouse, not a bare array like Lever),
`title`/`jobUrl` (not `text`/`hostedUrl`), and a primary `location` string
plus `secondaryLocations[].location` instead of one combined
`categories.allLocations` list.
"""

import datetime

import pytest

from jobscraper.job_boards import _http
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
            "compensation": {
                "scrapeableCompensationSalarySummary": "$150K - $200K",
                "compensationTierSummary": "$150K - $200K • Offers Equity",
            },
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


def test_fetch_ashby_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_ashby_jobs(
        "acme", "Acme Corp", "Big Tech", scraped_date=datetime.date(2026, 9, 12)
    )

    assert captured["url"] == f"{client.BASE_URL}/acme"
    # Ashby omits compensation unless it is explicitly requested.
    assert captured["params"] == {"includeCompensation": "true"}
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
    assert first.salary == "$150K - $200K"

    # Missing department/location/publishedAt degrade to "", compensation to None -- no crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""
    assert second.salary is None


def test_fetch_ashby_jobs_populates_description_only_for_a_firm_needing_it(
    monkeypatch,
):
    # No live Ashby board is in FIRMS_NEEDING_DESCRIPTION today -- monkeypatch
    # it onto the client's own binding to exercise the gating regardless.
    monkeypatch.setattr(client, "FIRMS_NEEDING_DESCRIPTION", frozenset({"Acme Corp"}))
    response = {
        "jobs": [
            {
                "id": "1",
                "title": "Engineer",
                "jobUrl": "https://x/1",
                "descriptionPlain": "Do the thing.",
            }
        ]
    }
    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FakeResponse(response))

    (needed,) = client.fetch_ashby_jobs("acme", "Acme Corp", "Big Tech")
    (not_needed,) = client.fetch_ashby_jobs("beta", "Beta Inc", "Big Tech")

    assert needed.description == "Do the thing."
    assert not_needed.description == ""  # not in FIRMS_NEEDING_DESCRIPTION


def test_fetch_ashby_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
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
        _http.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    jobs = client.fetch_ashby_jobs("acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "real-id"


def test_fetch_ashby_jobs_builds_location_from_primary_and_secondary_and_dedupes(
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
        _http.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_ashby_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "London, United Kingdom | New York, NY"


def test_fetch_ashby_jobs_falls_back_to_team_when_department_is_missing(monkeypatch):
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
        _http.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_ashby_jobs("acme", "Acme Corp", "Big Tech")

    assert job.department == "Administrative"


def test_fetch_ashby_jobs_raises_on_http_error(monkeypatch):
    # A persistent failure must propagate out of fetch_ashby_jobs -- main.py
    # is what decides that means "skip this board", not this client. The
    # retry/backoff policy itself is job_boards._http.get_with_retries' own
    # behavior -- see tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise _http.requests.HTTPError("boom")

    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(_http.requests.HTTPError):
        client.fetch_ashby_jobs("acme", "Acme Corp", "Big Tech")
