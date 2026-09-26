"""Tests for recruitee.client -- JSON-to-Job parsing, no real HTTP calls.

Same structure as tests/ashby/test_client.py, adapted to Recruitee's
response shape: {"offers": [...]}, `careers_url`, both `published_at` and
`updated_at`, and a pre-split `locations[]` list with its own `city` per
office. Field values mirror OGP's live board.
"""

import datetime

import pytest

from jobscraper.job_boards.recruitee import client

_SAMPLE_RESPONSE = {
    "offers": [
        {
            "id": 2756180,
            "title": "Principal / Lead Product Manager ",
            "department": "Product Management",
            "status": "published",
            "city": "Singapore",
            "country": "Singapore",
            "location": "Singapore, Central Singapore, Singapore",
            "locations": [
                {"name": "Singapore, Lazada One", "city": "Singapore", "country": "Singapore"}
            ],
            "published_at": "2026-09-23 10:00:07 UTC",
            "updated_at": "2026-09-24 08:15:00 UTC",
            "careers_url": "https://careers.open.gov.sg/o/principal-lead-product-manager",
            "careers_apply_url": "https://careers.open.gov.sg/o/principal-lead-product-manager/c/new",
            "description": "<h3>Who We Are</h3><p>OGP builds tech for public good.</p>",
            "requirements": "<ul><li>5+ years in product</li></ul>",
            "salary": {"min": None, "max": None, "period": None, "currency": None},
        },
        {
            "id": 2600001,
            "title": "Analyst Relations Lead",
            "department": "",
            "city": "Ghent",
            "locations": [
                {"city": "Ghent", "country": "Belgium"},
                {"city": "Singapore", "country": "Singapore"},
                {"city": "Singapore", "country": "Singapore"},
            ],
            "published_at": "",
            "updated_at": "",
            "careers_url": "https://x.recruitee.com/o/analyst-relations-lead",
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


def test_fetch_recruitee_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_recruitee_jobs(
        "opengovernmentproducts",
        "OGP",
        "Government/Defense Tech",
        scraped_date=datetime.date(2026, 9, 27),
    )

    assert captured["url"] == "https://opengovernmentproducts.recruitee.com/api/offers/"
    assert len(jobs) == 2
    job = jobs[0]
    assert job.board == "OGP"
    assert job.job_id == "2756180"
    assert job.title == "Principal / Lead Product Manager"
    assert job.department == "Product Management"
    assert job.location == "Singapore"
    assert job.url == "https://careers.open.gov.sg/o/principal-lead-product-manager"
    assert job.first_published == "2026-09-23"
    assert job.updated_at == "2026-09-24"
    assert job.scraped_date == datetime.date(2026, 9, 27)
    assert job.company_type == "Government/Defense Tech"
    assert job.source == "Recruitee"
    assert job.salary is None  # all-null on every live posting -- see module docstring
    assert job.description == ""  # OGP isn't in FIRMS_NEEDING_DESCRIPTION


def test_fetch_recruitee_jobs_joins_and_dedupes_every_office(monkeypatch):
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(_SAMPLE_RESPONSE)
    )

    jobs = client.fetch_recruitee_jobs("x", "X", "Big Tech")

    assert jobs[1].location == "Ghent | Singapore"
    assert jobs[1].department == ""
    assert jobs[1].first_published == ""
    assert jobs[1].updated_at == ""


def test_fetch_recruitee_jobs_falls_back_to_top_level_city(monkeypatch):
    response = {
        "offers": [
            {"id": 1, "title": "Engineer", "city": "Singapore", "locations": []}
        ]
    }
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_recruitee_jobs("x", "X", "Big Tech")

    assert job.location == "Singapore"


def test_fetch_recruitee_jobs_fills_description_only_for_listed_firms(monkeypatch):
    monkeypatch.setattr(
        client.requests, "get", lambda *a, **k: _FakeResponse(_SAMPLE_RESPONSE)
    )
    monkeypatch.setattr(client, "FIRMS_NEEDING_DESCRIPTION", frozenset({"OGP"}))

    job = client.fetch_recruitee_jobs("opengovernmentproducts", "OGP", "Gov")[0]

    assert "OGP builds tech for public good." in job.description
    assert "5+ years in product" in job.description
    assert "<" not in job.description


def test_fetch_recruitee_jobs_skips_a_record_without_an_id(monkeypatch):
    response = {"offers": [{"title": "No id"}, {"id": 7, "title": "Engineer"}]}
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    jobs = client.fetch_recruitee_jobs("x", "X", "Big Tech")

    assert [job.job_id for job in jobs] == ["7"]


def test_fetch_recruitee_jobs_raises_on_http_error(monkeypatch):
    # main.py decides a persistent failure means "skip this board"; the
    # retry policy itself is tested once in tests/job_boards/test_http.py.
    class _FailingResponse:
        status_code = 404

        def raise_for_status(self):
            raise client.requests.HTTPError("boom", response=self)

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_recruitee_jobs("x", "X", "Big Tech")
