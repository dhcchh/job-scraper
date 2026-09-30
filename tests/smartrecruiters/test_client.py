"""Tests for smartrecruiters.client using fake integrations."""

import datetime

import pytest

from jobscraper.job_boards import _http
from jobscraper.job_boards.smartrecruiters import client


def _page(content, offset=0, total_found=None):
    return {
        "offset": offset,
        "limit": 100,
        "totalFound": total_found if total_found is not None else len(content),
        "content": content,
    }


_SAMPLE_RESPONSE = _page(
    [
        {
            "id": "744000149061045",
            "name": "Senior Backend Engineer",
            "company": {"identifier": "Acme", "name": "Acme Corp"},
            "location": {"city": "Beijing", "country": "cn"},
            "function": {"id": "engineering", "label": "Engineering"},
            "releasedDate": "2025-06-15T09:00:00.026Z",
        },
        {
            "id": "744000148989219",
            "name": "No department or location",
            "company": {"identifier": "Acme", "name": "Acme Corp"},
            "location": {},
            "releasedDate": "",
        },
    ]
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_smartrecruiters_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_smartrecruiters_jobs(
        "Acme", "Acme Corp", "Big Tech", scraped_date=datetime.date(2026, 9, 12)
    )

    assert captured["url"] == f"{client.BASE_URL}/Acme/postings"
    assert captured["params"] == {"offset": 0, "limit": client._PAGE_LIMIT}
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Acme Corp"  # firm name, not the "Acme" identifier
    assert first.job_id == "744000149061045"
    assert first.department == "Engineering"  # from function.label
    assert first.location == "Beijing, China"
    assert first.url == "https://jobs.smartrecruiters.com/Acme/744000149061045"
    assert first.scraped_date == datetime.date(2026, 9, 12)
    assert first.seniority == "Senior"  # from "Senior Backend Engineer"
    assert first.company_type == "Big Tech"
    assert first.first_published == "2025-06-15"
    assert first.updated_at == ""  # no edited-at timestamp at all
    assert first.source == "SmartRecruiters"
    assert first.description == ""
    assert first.salary is None  # salary extraction is not implemented for this client

    # Missing function/location/releasedDate degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""


def test_fetch_smartrecruiters_jobs_paginates_until_total_found_is_reached(
    monkeypatch,
):
    page1 = _page(
        [{"id": str(i), "name": f"Job {i}", "company": {"identifier": "Acme"}} for i in range(100)],
        offset=0,
        total_found=150,
    )
    page2 = _page(
        [{"id": str(i), "name": f"Job {i}", "company": {"identifier": "Acme"}} for i in range(100, 150)],
        offset=100,
        total_found=150,
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return _FakeResponse(page1 if params["offset"] == 0 else page2)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 150
    assert calls == [
        {"offset": 0, "limit": client._PAGE_LIMIT},
        {"offset": 100, "limit": client._PAGE_LIMIT},
    ]


def test_fetch_smartrecruiters_jobs_rejects_an_empty_page_before_total(monkeypatch):
    # A partial snapshot must fail so main does not prune missing postings.
    page1 = _page(
        [{"id": "1", "name": "Job 1", "company": {"identifier": "Acme"}}],
        offset=0,
        total_found=5,
    )
    empty_page = _page([], offset=1, total_found=5)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return _FakeResponse(page1 if params["offset"] == 0 else empty_page)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    with pytest.raises(_http.IncompleteBoardResponse, match="empty page"):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert [call["offset"] for call in calls] == [0, 1, 0, 1]


def test_fetch_smartrecruiters_jobs_rejects_a_job_missing_id(
    monkeypatch,
):
    response = _page(
        [
            {"name": "No id field", "company": {"identifier": "Acme"}},
            {"id": "real-id", "name": "Software Engineer", "company": {"identifier": "Acme"}},
        ]
    )
    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FakeResponse(response))

    with pytest.raises(_http.IncompleteBoardResponse):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")


def test_fetch_smartrecruiters_jobs_falls_back_to_empty_department_when_function_is_missing(
    monkeypatch,
):
    response = _page(
        [{"id": "1", "name": "Administrative Business Partner", "company": {"identifier": "Acme"}}]
    )
    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert job.department == ""


def test_fetch_smartrecruiters_jobs_raises_on_http_error(monkeypatch):
    # A persistent failure must propagate out of fetch_smartrecruiters_jobs
    # -- main.py is what decides that means "skip this board", not this
    # client. The retry/backoff policy itself is
    # job_boards._http.get_with_retries' own behavior -- see
    # tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise _http.requests.HTTPError("boom")

    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(_http.requests.HTTPError):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")


@pytest.mark.parametrize("city, country, region, excluded", [
    ("Shenzhen", "cn", "PAC", False),
    ("Noida", "in", "RoW", True),
    ("Melbourne", "us", "RoW", False),
    ("", "sg", "PAC", False),
])
def test_location_uses_provider_country(city, country, region, excluded):
    from jobscraper.enrichment.classify_region import classify_region, is_excluded

    location = client._location({"location": {"city": city, "country": country}})
    assert classify_region(location) == region
    assert is_excluded(location) == excluded
