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


def _search_request(category_name):
    # The endpoint echoes its search as a JSON string; a known slug is
    # replaced by the category's name, an unknown one echoed unchanged.
    return json.dumps({"jobPostingSearchRequest": {
        "query": None,
        "filterFacets": [{"name": "category", "values": [{"name": category_name}]}],
        "sort": {"sortOrder": "DESCENDING", "sortType": "CREATED_DATE"},
    }})


def _page(jobs, hits=None, category_name="Software Development"):
    return {
        "error": None,
        "hits": len(jobs) if hits is None else hits,
        "jobs": jobs,
        "job_posting_search_request": _search_request(category_name),
    }


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


class _Listing:
    """A fake search endpoint paging a listing that can change between calls."""

    def __init__(self, monkeypatch, ids, after_call=None):
        self.jobs = [dict(_SEATTLE_JOB, id_icims=str(job_id)) for job_id in ids]
        self.offsets = []
        self._after_call = after_call
        monkeypatch.setattr(_http._session, "get", self._get)

    def _get(self, url, params=None, timeout=None):
        offset, limit = params["offset"], params["result_limit"]
        self.offsets.append(offset)
        payload = _page(self.jobs[offset:offset + limit], hits=len(self.jobs))
        if self._after_call is not None:
            self._after_call(self)
        return _FakeResponse(payload)

    def remove(self, *ids):
        self.jobs = [job for job in self.jobs if job["id_icims"] not in map(str, ids)]


def _small_pages(monkeypatch, size=4, overlap=2):
    monkeypatch.setattr(client, "_PAGE_SIZE", size)
    monkeypatch.setattr(client, "_PAGE_OVERLAP", overlap)


def _ids(jobs):
    return [job.job_id for job in jobs]


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


def test_fetch_amazon_jobs_pages_with_overlapping_offsets_until_hits(monkeypatch):
    _small_pages(monkeypatch)
    listing = _Listing(monkeypatch, range(10))

    fetched = _fetch()

    assert _ids(fetched) == [str(n) for n in range(10)]
    # Each page starts two postings before the previous one ended.
    assert listing.offsets == [0, 2, 4, 6]


def test_fetch_amazon_jobs_uses_the_real_page_size_and_overlap(monkeypatch):
    listing = _Listing(monkeypatch, range(250))

    assert _ids(_fetch()) == [str(n) for n in range(250)]
    assert listing.offsets == [0, 90, 180]


def test_fetch_amazon_jobs_stitches_across_a_posting_added_mid_fetch(monkeypatch):
    # A new posting at the top shifts every later page down by one; with
    # fixed offsets "3" would repeat and the total would change.
    _small_pages(monkeypatch)

    def add_once(listing):
        if len(listing.offsets) == 1:
            listing.jobs.insert(0, dict(_SEATTLE_JOB, id_icims="new"))

    _Listing(monkeypatch, range(10), add_once)

    assert _ids(_fetch()) == [str(n) for n in range(10)]


@pytest.mark.parametrize("closed, expected", [
    # Already fetched: kept in this snapshot, pruned on the next run.
    ("1", [str(n) for n in range(10)]),
    # Not fetched yet: absent, as it should be.
    ("5", [str(n) for n in range(10) if n != 5]),
])
def test_fetch_amazon_jobs_stitches_across_a_posting_closed_mid_fetch(
    monkeypatch, closed, expected
):
    # A closed posting ahead of the cursor shifts later pages up by one;
    # with fixed offsets a still-open posting would be skipped.
    _small_pages(monkeypatch)

    def close_once(listing):
        if len(listing.offsets) == 1:
            listing.remove(closed)

    _Listing(monkeypatch, range(10), close_once)

    assert _ids(_fetch()) == expected


def test_fetch_amazon_jobs_retries_a_shift_larger_than_the_overlap(monkeypatch):
    _small_pages(monkeypatch)

    def close_two_once(listing):
        if len(listing.offsets) == 1:
            listing.remove(0, 1)

    listing = _Listing(monkeypatch, range(10), close_two_once)

    assert _ids(_fetch()) == [str(n) for n in range(2, 10)]
    assert listing.offsets[:3] == [0, 2, 0]  # the second page lost "3"


def test_fetch_amazon_jobs_fails_when_the_listing_keeps_shifting(monkeypatch):
    _small_pages(monkeypatch)

    def close_the_top_two(listing):
        listing.jobs = listing.jobs[2:]

    _Listing(monkeypatch, range(20), close_the_top_two)

    with pytest.raises(_http.IncompleteBoardResponse, match="shifted"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_reordered_overlap(monkeypatch):
    # "b" moves behind the anchor between pages, so it would be seen twice.
    _small_pages(monkeypatch)
    a, b, c, d, e = (dict(_SEATTLE_JOB, id_icims=job_id) for job_id in "abcde")
    _serve(monkeypatch, {0: _page([a, b, c, d], hits=6), 2: _page([c, d, b, e], hits=6)})

    with pytest.raises(_http.IncompleteBoardResponse, match="duplicate"):
        _fetch()


def test_fetch_amazon_jobs_accepts_a_known_category_with_no_postings(monkeypatch):
    _serve(monkeypatch, {0: _page([], category_name="Research Science")})

    assert _fetch("research-science") == []


@pytest.mark.parametrize("payload", [
    # An unknown or renamed slug returns zero hits, not an error, and is
    # echoed unchanged; an empty snapshot would look like every posting closed.
    _page([], category_name="applied-sciencex"),
    {"error": None, "hits": 0, "jobs": []},
    {"error": None, "hits": 0, "jobs": [], "job_posting_search_request": "{not json"},
    {"error": None, "hits": 0, "jobs": [], "job_posting_search_request": "{}"},
])
def test_fetch_amazon_jobs_rejects_an_unrecognised_empty_category(monkeypatch, payload):
    _serve(monkeypatch, {0: payload})

    with pytest.raises(_http.IncompleteBoardResponse, match="recognise"):
        _fetch("applied-sciencex")


def test_fetch_amazon_jobs_rejects_a_category_at_the_hit_cap_before_paging(monkeypatch):
    monkeypatch.setattr(client, "_HIT_CAP", 500)
    calls = []
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB], hits=500)}, calls)

    with pytest.raises(_http.IncompleteBoardResponse, match="cap"):
        _fetch()
    assert {params["offset"] for _url, params in calls} == {0}


def test_fetch_amazon_jobs_rejects_a_short_page_before_hits(monkeypatch):
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB], hits=3)})

    with pytest.raises(_http.IncompleteBoardResponse, match="short page"):
        _fetch()


def test_fetch_amazon_jobs_rejects_a_page_longer_than_hits(monkeypatch):
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB, _SEATTLE_JOB], hits=1)})

    with pytest.raises(_http.IncompleteBoardResponse, match="past its total"):
        _fetch()


def test_fetch_amazon_jobs_rejects_an_empty_later_page(monkeypatch):
    _small_pages(monkeypatch)
    first = [dict(_SEATTLE_JOB, id_icims=str(n)) for n in range(4)]
    _serve(monkeypatch, {0: _page(first, hits=6), 2: _page([], hits=6)})

    with pytest.raises(_http.IncompleteBoardResponse, match="shifted"):
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


def test_fetch_amazon_jobs_rejects_a_duplicate_id_on_a_page(monkeypatch):
    _serve(monkeypatch, {0: _page([_SINGAPORE_JOB, _SINGAPORE_JOB])})

    with pytest.raises(_http.IncompleteBoardResponse, match="duplicate"):
        _fetch()


@pytest.mark.parametrize("locations, expected", [
    # The usable entry wins; the malformed one is skipped.
    (["{not json", _HYDERABAD], "Hyderabad, India"),
    (["[1, 2]", _HYDERABAD], "Hyderabad, India"),
    # With no usable entry, the top-level city and country stand in.
    (["{not json"], "Seattle, United States"),
    ("Seattle", "Seattle, United States"),
])
def test_fetch_amazon_jobs_skips_a_malformed_location_entry(monkeypatch, locations, expected):
    # An office isn't part of a posting's identity, so it can't fail the board.
    job = dict(_SEATTLE_JOB, locations=locations)
    _serve(monkeypatch, {0: _page([job])})

    assert [job.location for job in _fetch()] == [expected]


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


@pytest.mark.parametrize("value", [
    None, "", "18 days", "2026-11-06", 1762387200, ["November 6, 2026"], {"date": "x"},
])
def test_posted_date_degrades_to_empty(value):
    assert client._posted_date(value) == ""
