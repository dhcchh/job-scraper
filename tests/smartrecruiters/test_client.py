"""Tests for smartrecruiters.client -- JSON-to-Job parsing, no real HTTP
calls.

Mirrors tests/ashby/test_client.py's structure, adapted to SmartRecruiters'
response shape: paginated ({"offset", "limit", "totalFound", "content":
[...]}), `name`/constructed url (not `title`/a URL field), a single
`location` object (not a pre-split per-office array), and `function.label`
standing in for department (see client.py's module docstring for why).
"""

import datetime

import pytest

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

    monkeypatch.setattr(client.requests, "get", fake_get)

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
    assert first.description == ""  # never populated -- see module docstring

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

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 150
    assert calls == [
        {"offset": 0, "limit": client._PAGE_LIMIT},
        {"offset": 100, "limit": client._PAGE_LIMIT},
    ]


def test_fetch_smartrecruiters_jobs_stops_on_an_empty_page(monkeypatch):
    # A totalFound higher than what's actually returned must not loop
    # forever -- an empty content page ends pagination regardless.
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

    monkeypatch.setattr(client.requests, "get", fake_get)

    jobs = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert len(calls) == 2


def test_fetch_smartrecruiters_jobs_skips_a_job_missing_id_instead_of_raising(
    monkeypatch,
):
    response = _page(
        [
            {"name": "No id field", "company": {"identifier": "Acme"}},
            {"id": "real-id", "name": "Software Engineer", "company": {"identifier": "Acme"}},
        ]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    jobs = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(jobs) == 1
    assert jobs[0].job_id == "real-id"


def test_fetch_smartrecruiters_jobs_falls_back_to_empty_department_when_function_is_missing(
    monkeypatch,
):
    response = _page(
        [{"id": "1", "name": "Administrative Business Partner", "company": {"identifier": "Acme"}}]
    )
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse(response))

    (job,) = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert job.department == ""


def test_fetch_smartrecruiters_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise client.requests.HTTPError("boom")

    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(client.requests.HTTPError):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")


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


def test_fetch_smartrecruiters_jobs_retries_transient_error_then_succeeds(
    monkeypatch,
):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise client.requests.exceptions.ConnectionError("network blip")
        return _FakeResponse(_SAMPLE_RESPONSE)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    jobs = client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(calls) == 3  # 2 failures + 1 success, within _MAX_ATTEMPTS
    assert len(jobs) == 2


def test_fetch_smartrecruiters_jobs_gives_up_after_max_retries_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)

    with pytest.raises(client.requests.HTTPError):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(calls) == client._MAX_ATTEMPTS


def test_fetch_smartrecruiters_jobs_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(404)

    monkeypatch.setattr(client.requests, "get", fake_get)
    monkeypatch.setattr(
        client.time, "sleep", lambda seconds: pytest.fail("should not retry a 404")
    )

    with pytest.raises(client.requests.HTTPError):
        client.fetch_smartrecruiters_jobs("Acme", "Acme Corp", "Big Tech")

    assert len(calls) == 1  # no retries -- a 404 won't fix itself
