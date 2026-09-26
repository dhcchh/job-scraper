"""Tests for job_boards._http -- get_with_retries/post_with_retries (the retry
policy shared by every job_boards/*/client.py) and paginate_with_retries,
tested once here instead of once per board.

Each client's own test module (tests/greenhouse, tests/lever, tests/ashby,
tests/smartrecruiters, tests/eightfold, tests/workday) keeps a single
raises_on_http_error test to confirm it wires these in correctly and
propagates a persistent failure, but the retry-policy behavior itself
(retries/backoff/gives-up) only lives here now.
"""

import pytest

from jobscraper.job_boards import _http


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _RespondingWith:
    """A fake response that raises an HTTPError carrying the given status."""

    def __init__(self, status_code):
        self._status_code = status_code

    @property
    def status_code(self):
        return self._status_code

    def raise_for_status(self):
        error = _http.requests.HTTPError(f"{self._status_code} error")
        error.response = self
        raise error


def test_get_with_retries_returns_response_on_first_success(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(_http.requests, "get", fake_get)

    response = _http.get_with_retries(
        "greenhouse", "acme", "https://x/jobs", params={"content": "true"}
    )

    assert response.json() == {"ok": True}
    assert captured == {
        "url": "https://x/jobs",
        "params": {"content": "true"},
        "timeout": 30,
    }


def test_get_with_retries_defaults_params_to_none(monkeypatch):
    # Ashby's client calls get_with_retries with no params at all.
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr(_http.requests, "get", fake_get)

    _http.get_with_retries("ashby", "acme", "https://x/jobs")

    assert captured["params"] is None


@pytest.mark.parametrize(
    "exc_type",
    [_http.requests.exceptions.ConnectionError, _http.requests.exceptions.Timeout],
)
def test_get_with_retries_retries_a_connection_error_or_timeout_then_succeeds(
    monkeypatch, exc_type
):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise exc_type("blip")
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    response = _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 3  # 2 failures + 1 success, within MAX_ATTEMPTS
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("status_code", [500, 503, 429])
def test_get_with_retries_retries_a_5xx_or_429_then_succeeds(monkeypatch, status_code):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 2:
            return _RespondingWith(status_code)
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    response = _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 2
    assert response.json() == {"ok": True}


def test_get_with_retries_gives_up_after_max_attempts_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    with pytest.raises(_http.requests.HTTPError):
        _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == _http.MAX_ATTEMPTS


def test_get_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(404)

    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(
        _http.time, "sleep", lambda seconds: pytest.fail("should not retry a 404")
    )

    with pytest.raises(_http.requests.HTTPError):
        _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 1  # no retries -- a 404 won't fix itself


def test_get_with_retries_backs_off_exponentially(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _RespondingWith(503)

    delays = []
    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: delays.append(seconds))

    with pytest.raises(_http.requests.HTTPError):
        _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    # 2 retries within MAX_ATTEMPTS=3, doubling from BACKOFF_SECONDS.
    assert delays == [_http.BACKOFF_SECONDS, _http.BACKOFF_SECONDS * 2]


def test_get_with_retries_labels_the_retry_log_with_source_and_identifier(
    monkeypatch, caplog
):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 2:
            return _RespondingWith(503)
        return _FakeResponse()

    monkeypatch.setattr(_http.requests, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    with caplog.at_level("WARNING"):
        _http.get_with_retries("greenhouse", "wehrtyou", "https://x/jobs")

    assert "greenhouse[wehrtyou]: attempt 1/3 failed" in caplog.text


def test_post_with_retries_sends_json_and_retries_like_get(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        if len(calls) < 2:
            return _RespondingWith(503)
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(_http.requests, "post", fake_post)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    response = _http.post_with_retries(
        "workday", "visa.wd5/Visa", "https://x/jobs", json={"limit": 20}
    )

    assert response.json() == {"ok": True}
    assert calls == [("https://x/jobs", {"limit": 20}, 30)] * 2


def test_post_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return _RespondingWith(400)

    monkeypatch.setattr(_http.requests, "post", fake_post)
    monkeypatch.setattr(
        _http.time, "sleep", lambda seconds: pytest.fail("should not retry a 400")
    )

    with pytest.raises(_http.requests.HTTPError):
        _http.post_with_retries("workday", "visa.wd5/Visa", "https://x/jobs", json={})

    assert len(calls) == 1


def test_paginate_with_retries_posts_cursor_in_the_body_and_keeps_the_first_total(
    monkeypatch,
):
    # Workday's shape: `total` is only real on the first page and 0 on every
    # page after it -- the loop must keep going on the first page's total.
    pages = {
        0: {"total": 5, "jobPostings": [{"n": 0}, {"n": 1}]},
        2: {"total": 0, "jobPostings": [{"n": 2}, {"n": 3}]},
        4: {"total": 0, "jobPostings": [{"n": 4}]},
    }
    bodies = []

    def fake_post(url, json=None, timeout=None):
        bodies.append(json)
        return _FakeResponse(pages[json["offset"]])

    monkeypatch.setattr(_http.requests, "post", fake_post)
    monkeypatch.setattr(
        _http.requests, "get", lambda *a, **k: pytest.fail("should POST, not GET")
    )

    items = _http.paginate_with_retries(
        "workday",
        "visa.wd5/Visa",
        "https://x/jobs",
        method="POST",
        body={"appliedFacets": {}, "searchText": ""},
        cursor_param="offset",
        page_size_param="limit",
        page_size=2,
        total_key="total",
        items_key="jobPostings",
    )

    assert [item["n"] for item in items] == [0, 1, 2, 3, 4]
    assert bodies[0] == {"appliedFacets": {}, "searchText": "", "offset": 0, "limit": 2}
    assert [body["offset"] for body in bodies] == [0, 2, 4]


def test_paginate_with_retries_gets_cursor_as_query_params_by_default(monkeypatch):
    params_seen = []

    def fake_get(url, params=None, timeout=None):
        params_seen.append(params)
        if params["offset"] == 0:
            return _FakeResponse({"totalFound": 3, "content": [{"n": 0}, {"n": 1}]})
        return _FakeResponse({"totalFound": 3, "content": [{"n": 2}]})

    monkeypatch.setattr(_http.requests, "get", fake_get)

    items = _http.paginate_with_retries(
        "smartrecruiters",
        "Grab",
        "https://x/postings",
        cursor_param="offset",
        page_size_param="limit",
        page_size=2,
        total_key="totalFound",
        items_key="content",
    )

    assert [item["n"] for item in items] == [0, 1, 2]
    assert params_seen == [{"offset": 0, "limit": 2}, {"offset": 2, "limit": 2}]
