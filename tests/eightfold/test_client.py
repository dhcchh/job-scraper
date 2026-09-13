"""Tests for eightfold.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/smartrecruiters/test_client.py's structure, adapted to
Eightfold's response shape: paginated ({"count", "positions": [...]}) with a
server-enforced 10-per-page cap regardless of what's requested, a `domain`
query param required alongside the subdomain, and both `t_create`/`t_update`
as Unix epoch seconds (not ISO 8601 strings) -- see client.py's module
docstring for why each of these differs from the other four board clients.
"""

import datetime

import pytest

from jobscraper.job_boards.eightfold import client


def _page(positions, start=0, count=None):
    return {
        "start": start,
        "count": count if count is not None else len(positions),
        "positions": positions,
    }


_SAMPLE_RESPONSE = _page(
    [
        {
            "id": 755958456084,
            "name": "Senior Backend Engineer",
            "department": "Engineering",
            "business_unit": "Equity IT",
            "location": "Singapore, Singapore",
            "locations": ["Singapore, Singapore"],
            "ats_job_id": "REQ-1",
            "t_create": 1749945600,  # 2025-06-15T00:00:00Z
            "t_update": 1750032000,  # 2025-06-16T00:00:00Z
            "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/755958456084",
            "job_description": "",
        },
        {
            "id": 755958456085,
            "name": "No department or location",
            "locations": [],
            "t_create": None,
            "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/755958456085",
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


def test_fetch_eightfold_jobs_parses_response(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs(
        "mlp", "mlp.com", "Millennium", "Prop Trading/Hedge Fund",
        scraped_date=datetime.date(2026, 9, 12),
    )

    assert captured["url"] == "https://mlp.eightfold.ai/api/apply/v2/jobs"
    assert captured["params"] == {
        "domain": "mlp.com",
        "start": 0,
        "num": client._PAGE_LIMIT,
    }
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Millennium"  # firm name, not the "mlp" subdomain
    assert first.job_id == "755958456084"  # Eightfold's own id, not ats_job_id
    assert first.department == "Engineering"
    assert first.location == "Singapore"
    assert first.url == "https://mlp.eightfold.ai/careers/job/755958456084"
    assert first.scraped_date == datetime.date(2026, 9, 12)
    assert first.seniority == "Senior"  # from "Senior Backend Engineer"
    assert first.company_type == "Prop Trading/Hedge Fund"
    assert first.first_published == "2025-06-15"
    assert first.updated_at == "2025-06-16"  # unlike Lever/Ashby/SmartRecruiters
    assert first.source == "Eightfold"
    assert first.description == ""  # never populated -- see module docstring

    # Missing department/locations/t_create degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""
    assert second.updated_at == ""


def test_fetch_eightfold_jobs_falls_back_to_business_unit_when_department_is_missing(
    monkeypatch,
):
    response = _page(
        [
            {
                "id": 1,
                "name": "Administrative Business Partner",
                "business_unit": "Corporate Services",
                "locations": [],
                "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/1",
            }
        ]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert job.department == "Corporate Services"


def test_fetch_eightfold_jobs_falls_back_to_singular_location_field(monkeypatch):
    # A record missing `locations` entirely still has the singular
    # `location` field -- must not be dropped silently.
    response = _page(
        [
            {
                "id": 1,
                "name": "Engineer",
                "location": "Sydney, Australia",
                "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/1",
            }
        ]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert job.location == "Sydney, Australia"


def test_fetch_eightfold_jobs_dedupes_multi_office_locations(monkeypatch):
    response = _page(
        [
            {
                "id": 1,
                "name": "Engineer",
                "locations": ["Singapore, Singapore", "Singapore, Singapore"],
                "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/1",
            }
        ]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert job.location == "Singapore"


def test_fetch_eightfold_jobs_paginates_in_fixed_strides_of_ten_regardless_of_count(
    monkeypatch,
):
    # The server ignores num and always returns 10 per page -- confirmed
    # against live Millennium data (229 open postings, ~23 requests) -- see
    # client.py's module docstring.
    page1 = _page(
        [{"id": i, "name": f"Job {i}"} for i in range(10)], start=0, count=15
    )
    page2 = _page(
        [{"id": i, "name": f"Job {i}"} for i in range(10, 15)], start=10, count=15
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return _FakeResponse(page1 if params["start"] == 0 else page2)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(jobs) == 15
    assert calls == [
        {"domain": "mlp.com", "start": 0, "num": client._PAGE_LIMIT},
        {"domain": "mlp.com", "start": 10, "num": client._PAGE_LIMIT},
    ]


def test_fetch_eightfold_jobs_stops_on_an_empty_page(monkeypatch):
    # A count higher than what's actually returned must not loop forever --
    # an empty positions page ends pagination regardless.
    page1 = _page([{"id": 1, "name": "Job 1"}], start=0, count=5)
    empty_page = _page([], start=1, count=5)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return _FakeResponse(page1 if params["start"] == 0 else empty_page)

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(jobs) == 1
    assert len(calls) == 2


def test_fetch_eightfold_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
    response = _page(
        [
            {"name": "No id field"},
            {"id": 2, "name": "Software Engineer"},
        ]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    jobs = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "2"


def test_fetch_eightfold_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")


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


def test_fetch_eightfold_jobs_retries_transient_error_then_succeeds(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise client.requests.exceptions.ConnectionError("network blip")
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    jobs = client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(calls) == 3  # 2 failures + 1 success, within _MAX_ATTEMPTS
    assert len(jobs) == 2


def test_fetch_eightfold_jobs_gives_up_after_max_retries_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    with pytest.raises(client.requests.HTTPError):
        client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(calls) == client._MAX_ATTEMPTS


def test_fetch_eightfold_jobs_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(403)  # e.g. a mismatched domain/subdomain pairing

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(
        client.time, "sleep", lambda seconds: pytest.fail("should not retry a 403")
    )

    with pytest.raises(client.requests.HTTPError):
        client.fetch_eightfold_jobs("mlp", "mlp.com", "Millennium", "Big Tech")

    assert len(calls) == 1  # no retries -- a 403 won't fix itself
