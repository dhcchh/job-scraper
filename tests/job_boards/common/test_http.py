"""Tests for job_boards.common.http using fake integrations."""

import pytest

from jobscraper.job_boards.common import http
from tests.fakes import ErrorResponse, FakeResponse


@pytest.mark.parametrize("payload", [{}, {"jobs": None}, {"jobs": ["bad"]}])
def test_require_job_list_rejects_malformed_board_payload(payload):
    with pytest.raises(http.IncompleteBoardResponse):
        http.require_job_list(payload, "greenhouse", "acme", key="jobs")


def test_require_job_list_accepts_a_confirmed_empty_board():
    assert http.require_job_list({"jobs": []}, "greenhouse", "acme", key="jobs") == []


def test_get_with_retries_returns_response_on_first_success(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse({"ok": True})

    monkeypatch.setattr(http._session, "get", fake_get)

    response = http.get_with_retries(
        "greenhouse", "acme", "https://x/jobs", params={"content": "true"}
    )

    assert response.json() == {"ok": True}
    assert captured == {
        "url": "https://x/jobs",
        "params": {"content": "true"},
        "timeout": 30,
    }


def test_get_with_retries_defaults_params_to_none(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(http._session, "get", fake_get)

    http.get_with_retries("ashby", "acme", "https://x/jobs")

    assert captured["params"] is None


@pytest.mark.parametrize(
    "exc_type",
    [http.requests.exceptions.ConnectionError, http.requests.exceptions.Timeout],
)
def test_get_with_retries_retries_a_connection_error_or_timeout_then_succeeds(
    monkeypatch, exc_type
):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise exc_type("blip")
        return FakeResponse({"ok": True})

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    response = http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 3  # 2 failures + 1 success, within MAX_ATTEMPTS
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("status_code", [500, 503, 429])
def test_get_with_retries_retries_a_5xx_or_429_then_succeeds(monkeypatch, status_code):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 2:
            return ErrorResponse(status_code)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    response = http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 2
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("status_code, retry_after, expected", [
    (429, "7", 7.0),
    (429, "3600", http.MAX_RETRY_AFTER_SECONDS),
    (429, "Wed, 21 Oct 2026 07:28:00 GMT", http.BACKOFF_SECONDS),
    (429, "nan", http.BACKOFF_SECONDS),
    (503, "7", http.BACKOFF_SECONDS),
])
def test_get_with_retries_honors_a_429_retry_after_in_seconds(
    monkeypatch, status_code, retry_after, expected
):
    responses = [
        ErrorResponse(status_code, {"Retry-After": retry_after}),
        FakeResponse({"ok": True}),
    ]
    sleeps = []
    monkeypatch.setattr(http._session, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(http.time, "sleep", sleeps.append)

    http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert sleeps == [expected]


def test_board_requests_share_one_session_with_a_user_agent():
    assert http._session.headers["User-Agent"] == http.USER_AGENT


def test_get_with_retries_gives_up_after_max_attempts_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return ErrorResponse(503)

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    with pytest.raises(http.requests.HTTPError):
        http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == http.MAX_ATTEMPTS


def test_get_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return ErrorResponse(404)

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(
        http.time, "sleep", lambda seconds: pytest.fail("should not retry a 404")
    )

    with pytest.raises(http.requests.HTTPError):
        http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 1  # no retries -- a 404 won't fix itself


def test_get_with_retries_backs_off_exponentially(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return ErrorResponse(503)

    delays = []
    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: delays.append(seconds))

    with pytest.raises(http.requests.HTTPError):
        http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    # 2 retries within MAX_ATTEMPTS=3, doubling from BACKOFF_SECONDS.
    assert delays == [http.BACKOFF_SECONDS, http.BACKOFF_SECONDS * 2]


def test_get_with_retries_labels_the_retry_log_with_source_and_identifier(
    monkeypatch, caplog
):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) < 2:
            return ErrorResponse(503)
        return FakeResponse()

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    with caplog.at_level("WARNING"):
        http.get_with_retries("greenhouse", "wehrtyou", "https://x/jobs")

    assert "greenhouse[wehrtyou]: attempt 1/3 failed" in caplog.text


def test_post_with_retries_sends_json_and_retries_like_get(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        if len(calls) < 2:
            return ErrorResponse(503)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(http._session, "post", fake_post)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    response = http.post_with_retries("apple", "SFTWR", "https://x/jobs", json={"page": 1})

    assert response.json() == {"ok": True}
    assert calls == [("https://x/jobs", {"page": 1}, 30)] * 2


def test_post_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return ErrorResponse(400)

    monkeypatch.setattr(http._session, "post", fake_post)
    monkeypatch.setattr(
        http.time, "sleep", lambda seconds: pytest.fail("should not retry a 400")
    )

    with pytest.raises(http.requests.HTTPError):
        http.post_with_retries("apple", "SFTWR", "https://x/jobs", json={})

    assert len(calls) == 1
