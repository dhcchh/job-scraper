"""Tests for eightfold.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/smartrecruiters/test_client.py's structure, adapted to
Eightfold's response shape (confirmed against Millennium's live board): a
`{"count", "positions": [...]}` payload paginated via `start`/`num` where
`num` isn't honored (always 10 per page), `name`/pre-built
`canonicalPositionUrl`, a `locations` array of "City, Region, Country"-shaped
strings (not bare city names), a plain top-level `department` string, and
both `t_create`/`t_update` epoch timestamps (unlike SmartRecruiters/Lever/
Ashby's no-edited-at-signal shape).
"""

import datetime

import pytest

from jobscraper.job_boards import _http
from jobscraper.job_boards.eightfold import client


def _page(positions, start=0, count=None):
    return {
        "count": count if count is not None else len(positions),
        "positions": positions,
    }


_SAMPLE_RESPONSE = _page(
    [
        {
            "id": 755958456084,
            "name": "Linux Low Latency Systems Engineer",
            "department": "Information Technology",
            "locations": ["Singapore, Singapore"],
            "canonicalPositionUrl": "https://mlp.eightfold.ai/careers/job/755958456084",
            "t_create": 1787875200,
            "t_update": 1787875200,
        },
        {
            "id": 755930336226,
            "name": "No department or location",
            "locations": [],
            "t_create": 0,
            "t_update": 0,
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

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs(
        "mlp", "Millennium", "Prop Trading/Hedge Fund", scraped_date=datetime.date(2026, 9, 16)
    )

    assert captured["url"] == "https://mlp.eightfold.ai/api/apply/v2/jobs"
    assert captured["params"] == {"start": 0, "num": client._PAGE_SIZE}
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Millennium"
    assert first.job_id == "755958456084"
    assert first.department == "Information Technology"
    assert first.location == "Singapore"
    assert first.url == "https://mlp.eightfold.ai/careers/job/755958456084"
    assert first.scraped_date == datetime.date(2026, 9, 16)
    assert first.company_type == "Prop Trading/Hedge Fund"
    assert first.first_published == "2026-08-28"  # from t_create, epoch seconds
    assert first.updated_at == "2026-08-28"  # from t_update, epoch seconds
    assert first.source == "Eightfold"
    assert first.description == ""
    assert first.salary is None  # no compensation field on any posting

    # Missing department/locations and zero timestamps degrade to "", not a crash.
    assert second.department == ""
    assert second.location == ""
    assert second.seniority == "Unspecified"
    assert second.first_published == ""
    assert second.updated_at == ""


def test_fetch_eightfold_jobs_collapses_duplicated_city_and_full_country_name():
    # Eightfold's own "City, City, Country" duplication ("Tokyo, Tokyo,
    # Japan") and full country names ("United Arab Emirates") are collapsed
    # to this project's existing conventions by keeping only the leading
    # city segment and re-deriving the rest from normalize_location.py's
    # table -- confirmed against Millennium's live board.
    raw = {
        "id": 1,
        "name": "Deep Learning Quantitative Researcher",
        "locations": [
            "Hong Kong, Hong Kong",
            "Tokyo, Tokyo, Japan",
            "Dubai, United Arab Emirates",
            "Singapore, Singapore",
            "Shanghai, Shanghai, China",
        ],
        "t_create": 0,
        "t_update": 0,
    }
    job = client._to_job(raw, "Millennium", "Prop Trading/Hedge Fund", datetime.date(2026, 9, 16))

    assert job.location == (
        "Hong Kong | Tokyo, Japan | Dubai, UAE | Singapore | Shanghai, China"
    )


def test_fetch_eightfold_jobs_falls_back_to_singular_location_field():
    raw = {
        "id": 2,
        "name": "Some Role",
        "location": "Dublin, Ireland",
        "t_create": 0,
        "t_update": 0,
    }
    job = client._to_job(raw, "Millennium", "Prop Trading/Hedge Fund", datetime.date(2026, 9, 16))

    assert job.location == "Dublin, Ireland"


def test_fetch_eightfold_jobs_pages_in_fixed_strides_regardless_of_count(monkeypatch):
    # The server always returns exactly _PAGE_SIZE positions per page no
    # matter what's requested -- confirmed against Millennium's live board --
    # so pagination must stop from `count`/an empty page, not a returned
    # page shorter than what was asked for.
    page1 = _page(
        [{"id": i, "name": f"Job {i}", "t_create": 0, "t_update": 0} for i in range(10)],
        count=25,
    )
    page2 = _page(
        [{"id": i, "name": f"Job {i}", "t_create": 0, "t_update": 0} for i in range(10, 20)],
        count=25,
    )
    page3 = _page(
        [{"id": i, "name": f"Job {i}", "t_create": 0, "t_update": 0} for i in range(20, 25)],
        count=25,
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        start = params["start"]
        if start == 0:
            return _FakeResponse(page1)
        if start == 10:
            return _FakeResponse(page2)
        return _FakeResponse(page3)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs("mlp", "Millennium", "Prop Trading/Hedge Fund")

    assert len(jobs) == 25
    assert calls == [
        {"start": 0, "num": client._PAGE_SIZE},
        {"start": 10, "num": client._PAGE_SIZE},
        {"start": 20, "num": client._PAGE_SIZE},
    ]


def test_fetch_eightfold_jobs_stops_on_an_empty_page(monkeypatch):
    # A count higher than what's actually returned must not loop forever --
    # an empty positions page ends pagination regardless.
    page1 = _page([{"id": 1, "name": "Job 1", "t_create": 0, "t_update": 0}], count=5)
    empty_page = _page([], count=5)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return _FakeResponse(page1 if params["start"] == 0 else empty_page)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs("mlp", "Millennium", "Prop Trading/Hedge Fund")

    assert len(jobs) == 1
    assert len(calls) == 2


def test_fetch_eightfold_jobs_skips_a_job_missing_id_instead_of_raising(monkeypatch):
    response = _page(
        [
            {"name": "No id field", "t_create": 0, "t_update": 0},
            {"id": "real-id", "name": "Software Engineer", "t_create": 0, "t_update": 0},
        ]
    )
    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FakeResponse(response))

    jobs = client.fetch_eightfold_jobs("mlp", "Millennium", "Prop Trading/Hedge Fund")

    assert len(jobs) == 1
    assert jobs[0].job_id == "real-id"


def test_fetch_eightfold_jobs_skips_a_duplicate_id_across_pages(monkeypatch):
    # Unconfirmed whether Eightfold's server ever pads a short final page
    # with an already-seen id instead of returning fewer than _PAGE_SIZE --
    # if it does, this must not append the same (board, job_id) twice.
    page1 = _page(
        [{"id": 1, "name": "Job 1", "t_create": 0, "t_update": 0}], count=2
    )
    page2 = _page(
        [{"id": 1, "name": "Job 1", "t_create": 0, "t_update": 0}], count=2
    )
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(page1 if calls["n"] == 1 else page2)

    monkeypatch.setattr(_http.requests, "get", fake_get)

    jobs = client.fetch_eightfold_jobs("mlp", "Millennium", "Prop Trading/Hedge Fund")

    assert len(jobs) == 1
    assert jobs[0].job_id == "1"


def test_epoch_date_tolerates_a_numeric_string(monkeypatch):
    # Real-data shape is an int; a numeric string is tolerated the same way
    # rather than raising and failing the whole board's fetch over one
    # field's type.
    assert client._epoch_date("1787875200") == client._epoch_date(1787875200)


def test_epoch_date_degrades_to_empty_on_non_numeric_value():
    assert client._epoch_date("not-a-timestamp") == ""


def test_fetch_eightfold_jobs_raises_on_http_error(monkeypatch):
    # A persistent failure must propagate out of fetch_eightfold_jobs --
    # main.py is what decides that means "skip this board", not this
    # client. The retry/backoff policy itself is
    # job_boards._http.get_with_retries' own behavior -- see
    # tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise _http.requests.HTTPError("boom")

    monkeypatch.setattr(_http.requests, "get", lambda *a, **k: _FailingResponse())

    with pytest.raises(_http.requests.HTTPError):
        client.fetch_eightfold_jobs("mlp", "Millennium", "Prop Trading/Hedge Fund")
