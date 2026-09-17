"""Tests for lever.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/greenhouse/test_client.py's structure, adapted to Lever's
response shape: a bare JSON array (not {"jobs": [...]}), `text`/`hostedUrl`
instead of `title`/`absolute_url`, and `categories.{department,team,
location,allLocations}` instead of `departments`/`location`/`offices`.
"""

import datetime

import pytest

from jobscraper.job_boards.lever import client

_SAMPLE_RESPONSE = [
    {
        "id": "ac978161-6f46-4f6b-ad9e-a258e642751c",
        "text": "Senior Backend Engineer",
        "createdAt": 1749999600000,  # 2025-06-15T09:00:00Z
        "hostedUrl": "https://jobs.lever.co/acme/ac978161",
        "categories": {
            "department": "Engineering",
            "team": "Platform",
            "location": "London",
            "allLocations": ["London"],
        },
        "salaryRange": {
            "min": 55000,
            "max": 65000,
            "currency": "USD",
            "interval": "per-year-salary",
        },
    },
    {
        "id": "10dfc8bc-99ad-4ca2-ab76-853cb90a92c2",
        "text": "No department or location",
        "createdAt": None,
        "hostedUrl": "https://jobs.lever.co/acme/10dfc8bc",
        "categories": {},
    },
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_lever_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_lever_jobs(
        "acme", "Acme Corp", "Big Tech", scraped_date=datetime.date(2026, 9, 12)
    )

    assert captured["url"] == f"{client.BASE_URL}/acme"
    assert captured["params"] == {"mode": "json"}
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Acme Corp"  # firm name, not the "acme" board slug
    assert first.job_id == "ac978161-6f46-4f6b-ad9e-a258e642751c"
    assert first.department == "Engineering"
    assert first.location == "London, United Kingdom"
    assert first.scraped_date == datetime.date(2026, 9, 12)
    assert first.seniority == "Senior"  # from "Senior Backend Engineer"
    assert first.company_type == "Big Tech"
    assert first.first_published == "2025-06-15"
    assert first.updated_at == ""  # Lever's API has no edited-at timestamp at all
    assert first.salary == "USD 55,000-65,000/year"

    # Missing department/location/createdAt/salaryRange degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""
    assert second.salary == ""


def test_fetch_lever_jobs_populates_description_only_for_a_firm_needing_it(
    monkeypatch,
):
    # No live Lever board is in FIRMS_NEEDING_DESCRIPTION today -- monkeypatch
    # it onto the client's own binding to exercise the gating regardless.
    monkeypatch.setattr(client, "FIRMS_NEEDING_DESCRIPTION", frozenset({"Acme Corp"}))
    response = [
        {
            "id": "1",
            "text": "Engineer",
            "createdAt": None,
            "hostedUrl": "https://x/1",
            "categories": {},
            "openingPlain": "Do the thing.",
        }
    ]
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (needed,) = client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")
    (not_needed,) = client.fetch_lever_jobs("beta", "Beta Inc", "Big Tech")

    assert needed.description == "Do the thing."
    assert not_needed.description == ""  # not in FIRMS_NEEDING_DESCRIPTION


def test_fetch_lever_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
    response = [
        {
            "text": "No id field",
            "hostedUrl": "https://x/bad",
            "categories": {},
        },
        {
            "id": "real-id",
            "text": "Software Engineer",
            "hostedUrl": "https://x/real-id",
            "categories": {},
        },
    ]
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    jobs = client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "real-id"


def test_fetch_lever_jobs_builds_location_from_all_locations_and_dedupes(monkeypatch):
    response = [
        {
            "id": "1",
            "text": "Software Engineer",
            "hostedUrl": "https://x/1",
            "categories": {
                "location": "London",
                "allLocations": ["London", "New York City", "NYC"],
            },
        }
    ]
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "London, United Kingdom | New York, NY"


def test_fetch_lever_jobs_falls_back_to_categories_location_when_all_locations_missing(
    monkeypatch,
):
    response = [
        {
            "id": "1",
            "text": "Software Engineer",
            "hostedUrl": "https://x/1",
            "categories": {"location": "Bangalore", "allLocations": []},
        }
    ]
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")

    assert job.location == "Bangalore, India"


def test_fetch_lever_jobs_falls_back_to_team_when_department_is_missing(monkeypatch):
    # Confirmed on Palantir's live Lever board: `categories.department` is
    # absent entirely on every posting, only `categories.team` is set.
    response = [
        {
            "id": "1",
            "text": "Administrative Business Partner",
            "hostedUrl": "https://x/1",
            "categories": {"team": "Administrative"},
        }
    ]
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(response)
    )

    (job,) = client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")

    assert job.department == "Administrative"


def test_fetch_lever_jobs_raises_on_http_error(monkeypatch):
    # A persistent failure must propagate out of fetch_lever_jobs -- main.py
    # is what decides that means "skip this board", not this client. The
    # retry/backoff policy itself is job_boards._http.get_with_retries' own
    # behavior -- see tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_lever_jobs("acme", "Acme Corp", "Big Tech")


def test_salary_collapses_a_single_value_range():
    # min == max -- must not render as "55,000-55,000".
    raw = {"salaryRange": {"min": 80000, "max": 80000, "currency": "USD", "interval": "per-year-salary"}}
    assert client._salary(raw) == "USD 80,000/year"


def test_salary_keeps_an_interval_shaped_unlike_per_x_salary_as_is():
    raw = {"salaryRange": {"min": 20, "max": 30, "currency": "USD", "interval": "hourly"}}
    assert client._salary(raw) == "USD 20-30/hourly"


def test_salary_missing_salary_range_is_empty_string():
    assert client._salary({}) == ""
