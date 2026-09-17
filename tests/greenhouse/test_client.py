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


def test_fetch_greenhouse_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_greenhouse_jobs(
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
    assert first.first_published == "2026-06-15"
    assert first.updated_at == "2026-09-01"
    assert first.salary == ""  # Greenhouse has no structured pay field at all

    # Missing department/location degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""  # missing from the raw job, not a crash


def test_fetch_greenhouse_jobs_populates_description_only_for_a_firm_needing_it(
    monkeypatch,
):
    response = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "updated_at": "",
                "absolute_url": "https://x/1",
                "location": {},
                "departments": [],
                "content": "&lt;p&gt;Do the thing.&lt;/p&gt;",
            }
        ]
    }
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (needed,) = client.fetch_greenhouse_jobs("qrt", "QRT", "Prop Trading/Hedge Fund")
    (not_needed,) = client.fetch_greenhouse_jobs("acme", "Acme Corp", "Big Tech")

    assert needed.description == "Do the thing."
    assert not_needed.description == ""  # not in FIRMS_NEEDING_DESCRIPTION


def test_fetch_greenhouse_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
    # A malformed record with no id at all must not cost the whole board's
    # otherwise-good postings for the day -- main.py treats any exception
    # from fetch_greenhouse_jobs as this entire board having failed to fetch.
    response = {
        "jobs": [
            {
                "title": "No id field",
                "updated_at": "",
                "absolute_url": "https://x/bad",
                "location": {},
                "departments": [],
            },
            {
                "id": 99,
                "title": "Software Engineer",
                "updated_at": "",
                "absolute_url": "https://x/99",
                "location": {},
                "departments": [],
            },
        ]
    }
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    jobs = client.fetch_greenhouse_jobs("acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "99"


def test_fetch_greenhouse_jobs_builds_location_from_offices_not_the_raw_location_string(monkeypatch):
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

    (job,) = client.fetch_greenhouse_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "London, United Kingdom | New York, NY"


def test_fetch_greenhouse_jobs_falls_back_to_location_name_when_offices_missing(monkeypatch):
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

    (job,) = client.fetch_greenhouse_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "Bangalore, India"  # still normalized via the fallback


def test_fetch_greenhouse_jobs_raises_on_http_error(monkeypatch):
    # A persistent failure must propagate out of fetch_greenhouse_jobs --
    # main.py is what decides that means "skip this board", not this client.
    # The retry/backoff policy itself is job_boards._http.get_with_retries'
    # own behavior -- see tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_greenhouse_jobs("acme", "Acme Corp", "Big Tech")
