"""Tests for amazon.client using fake integrations.

Fixture records are trimmed copies of real amazon.jobs search.json postings
(2026-11-08); `locations` entries are JSON strings, as the endpoint returns.
"""

import datetime
import json

import pytest

from jobscraper.enrichment.classify_region import classify_region, is_excluded
from jobscraper.job_boards import _http
from jobscraper.job_boards.amazon import client


def _entry(city, iso3, country_name, normalized_city=None):
    entry = {
        "city": city,
        "countryIso3a": iso3,
        "normalizedCountryCode": iso3,
        "normalizedCountryName": country_name,
        "type": "ONSITE",
    }
    if normalized_city is not None:
        entry["normalizedCityName"] = normalized_city
    return json.dumps(entry)


_SEATTLE = _entry("Seattle", "USA", "United States", "Seattle")
_SEATTLE_CAPS = _entry("SEATTLE", "USA", "United States", "Seattle")
_HYDERABAD = _entry("Hyderabad", "IND", "India", "Hyderabad")

_SINGAPORE_JOB = {
    "id": "f4c10857-563c-4224-b449-826da539e78f",
    "id_icims": "10584792",
    "title": "Sr Forward Deployed Engineer, AWS Forward Deployed Engineering",
    "job_family": "Software Development",
    "job_category": "Software Development",
    "business_category": "aws",
    "job_path": "/en/jobs/10584792/sr-forward-deployed-engineer-aws-forward-deployed-engineering",
    "posted_date": "October 22, 2026",
    "updated_time": "18 days",
    "city": "Singapore",
    "country_code": "SGP",
    "normalized_location": "SGP",
    "locations": [_entry("Singapore", "SGP", "Singapore")],
    "description": "A Forward Deployed Engineer (FDE) embeds directly inside a strategic enterprise customer.",
    "basic_qualifications": "- 5+ years of leading design or architecture<br/>- Experience as a mentor",
    "preferred_qualifications": "- Bachelor's degree in computer science or equivalent",
}

_SEATTLE_JOB = {
    "id_icims": "10598335",
    "title": "Software Development Engineer",
    "job_family": "Software Development",
    "job_path": "/en/jobs/10598335/software-development-engineer",
    "posted_date": "November  6, 2026",
    "city": "Seattle",
    "country_code": "USA",
    "locations": [_SEATTLE, _SEATTLE_CAPS],
}

_INDIA_JOB = {
    "id_icims": "10599171",
    "title": "Software Dev Engineer II, Finance Technology ",
    "job_family": "Software Development",
    "job_path": "/en/jobs/10599171/software-dev-engineer-ii-finance-technology",
    "posted_date": "November  6, 2026",
    "city": "Hyderabad",
    "country_code": "IND",
    "normalized_location": "Hyderabad, Telangana, IND",
    "locations": [_HYDERABAD],
}


def _page(jobs, hits=None):
    return {"error": None, "hits": len(jobs) if hits is None else hits, "jobs": jobs}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _serve(monkeypatch, pages_by_offset, calls=None):
    def fake_get(url, params=None, timeout=None):
        if calls is not None:
            calls.append((url, dict(params)))
        return _FakeResponse(pages_by_offset[params["offset"]])

    monkeypatch.setattr(_http._session, "get", fake_get)


def _fetch(category="software-development", firm="Amazon"):
    return client.fetch_amazon_jobs(
        category, firm, "Consumer/Marketplace", scraped_date=datetime.date(2026, 11, 8)
    )


def test_fetch_amazon_jobs_parses_response(monkeypatch):
    calls = []
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB, _SEATTLE_JOB, _INDIA_JOB])}, calls)

    jobs = _fetch()

    url, params = calls[0]
    assert url == (
        "https://www.amazon.jobs/en/search.json"
        "?category%5B%5D=software-development&sort=recent"
    )
    assert params == {"offset": 0, "result_limit": client._PAGE_SIZE}

    singapore, seattle, india = jobs
    assert singapore.board == "Amazon"
    assert singapore.job_id == "10584792"
    assert singapore.title == "Sr Forward Deployed Engineer, AWS Forward Deployed Engineering"
    assert singapore.department == "Software Development"
    assert singapore.location == "Singapore"
    assert singapore.url == (
        "https://www.amazon.jobs/en/jobs/10584792/"
        "sr-forward-deployed-engineer-aws-forward-deployed-engineering"
    )
    assert singapore.first_published == "2026-10-22"
    assert singapore.updated_at == ""  # updated_time is relative ("18 days")
    assert singapore.scraped_date == datetime.date(2026, 11, 8)
    assert singapore.company_type == "Consumer/Marketplace"
    assert singapore.source == "Amazon"
    assert singapore.seniority == "Senior"
    assert singapore.description == ""  # Amazon is not on the allowlist
    assert singapore.salary is None

    # The padded day ("November  6") parses, and a capitalized duplicate of
    # the same office collapses into one.
    assert seattle.first_published == "2026-11-06"
    assert seattle.location == "Seattle, United States"

    assert india.location == "Hyderabad, India"
    assert is_excluded(india.location)


def test_fetch_amazon_jobs_pages_by_offset_until_hits(monkeypatch):
    jobs = [dict(_SEATTLE_JOB, id_icims=str(n)) for n in range(250)]
    pages = {offset: _page(jobs[offset:offset + 100], hits=250) for offset in (0, 100, 200)}
    calls = []
    _serve(monkeypatch, pages, calls)

    fetched = _fetch()

    assert [job.job_id for job in fetched] == [str(n) for n in range(250)]
    assert sorted(params["offset"] for _url, params in calls) == [0, 100, 200]


def test_fetch_amazon_jobs_rejects_zero_hits(monkeypatch):
    # An unknown or renamed category slug returns zero hits, not an error;
    # an empty snapshot would otherwise look like every posting closed.
    _serve(monkeypatch, {0: _page([])})

    with pytest.raises(_http.IncompleteBoardResponse, match="no postings"):
        _fetch("applied-science")


def test_fetch_amazon_jobs_rejects_a_category_at_the_hit_cap(monkeypatch):
    monkeypatch.setattr(client, "_HIT_CAP", 2)
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB, _SEATTLE_JOB])})

    with pytest.raises(_http.IncompleteBoardResponse, match="cap"):
        _fetch()


def test_fetch_amazon_jobs_rejects_an_empty_page_before_hits(monkeypatch):
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB], hits=3), 1: _page([], hits=3), 2: _page([], hits=3)})

    with pytest.raises(_http.IncompleteBoardResponse, match="empty page"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_missing_jobs_list(monkeypatch):
    # Past the cap the endpoint returns "jobs": null.
    _serve(monkeypatch, {0: {"error": None, "hits": 1, "jobs": None}})

    with pytest.raises(_http.IncompleteBoardResponse, match="invalid jobs list"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_job_missing_id(monkeypatch):
    job = {key: value for key, value in _SEATTLE_JOB.items() if key != "id_icims"}
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB, job])})

    with pytest.raises(_http.IncompleteBoardResponse, match="id_icims"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_duplicate_id_across_pages(monkeypatch):
    pages = {
        0: _page([_SINGAPORE_JOB], hits=2),
        1: _page([_SINGAPORE_JOB], hits=2),
    }
    _serve(monkeypatch, pages)

    with pytest.raises(_http.IncompleteBoardResponse, match="duplicate"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_malformed_location_entry(monkeypatch):
    job = dict(_SEATTLE_JOB, locations=["{not json"])
    _serve(monkeypatch, {0: _page([job])})

    with pytest.raises(_http.IncompleteBoardResponse, match="malformed location"):
        _fetch()


def test_fetch_amazon_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise _http.requests.HTTPError("boom")

    monkeypatch.setattr(_http._session, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(_http.requests.HTTPError):
        _fetch()


def test_description_combines_description_and_qualifications_for_allowlisted_firm(monkeypatch):
    monkeypatch.setattr(client, "FIRMS_NEEDING_DESCRIPTION", frozenset({"Amazon"}))

    job = client._to_job(_SINGAPORE_JOB, "Amazon", "Consumer/Marketplace", datetime.date(2026, 11, 8))

    assert job.description == (
        "A Forward Deployed Engineer (FDE) embeds directly inside a strategic enterprise customer."
        "\n\n- 5+ years of leading design or architecture\n- Experience as a mentor"
        "\n\n- Bachelor's degree in computer science or equivalent"
    )


@pytest.mark.parametrize("entries, location, region, excluded", [
    # Hong Kong and China are PAC by code; Amazon names them "Hong Kong SAR"
    # and "People's Republic of China".
    ([_entry("Hong Kong", "HKG", "Hong Kong SAR")], "Hong Kong", "PAC", False),
    (
        [_entry("Shanghai", "CHN", "People's Republic of China", "Shanghai")],
        "Shanghai, China",
        "PAC",
        False,
    ),
    # A code the shared table lacks falls back to Amazon's country name.
    ([_entry("Iasi", "ROU", "Romania", "Iasi")], "Iasi, Romania", "RoW", False),
    # India is excluded only when every office is in India.
    ([_HYDERABAD], "Hyderabad, India", "RoW", True),
    ([_HYDERABAD, _SEATTLE], "Hyderabad, India | Seattle, United States", "RoW", False),
])
def test_location_keeps_country_and_region(entries, location, region, excluded):
    result = client._location({"locations": entries}, "1")

    assert result == location
    assert classify_region(result) == region
    assert is_excluded(result) == excluded


def test_location_ignores_a_normalized_city_naming_another_place():
    # Amazon's normalizedCityName can name a different place.
    entry = _entry("Arizona", "USA", "United States", "Arizona City")

    assert client._location({"locations": [entry]}, "1") == "Arizona, United States"


def test_location_falls_back_to_top_level_city_and_country():
    raw = {"city": "Singapore", "country_code": "SGP", "locations": []}

    assert client._location(raw, "1") == "Singapore"


def test_url_falls_back_to_the_job_id_without_a_path():
    assert client._url({}, "10584792") == "https://www.amazon.jobs/en/jobs/10584792"


@pytest.mark.parametrize("value", [None, "", "18 days", "2026-11-06"])
def test_posted_date_degrades_to_empty(value):
    assert client._posted_date(value) == ""
