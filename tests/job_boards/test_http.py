"""Tests for job_boards.http using fake integrations."""

import pytest

from jobscraper.job_boards import _http
from tests.fakes import FakeResponse


class _RespondingWith:
    """A fake response that raises an HTTPError carrying the given status."""

    def __init__(self, status_code, headers=None):
        self._status_code = status_code
        self.headers = headers or {}

    @property
    def status_code(self):
        return self._status_code

    def raise_for_status(self):
        error = _http.requests.HTTPError(f"{self._status_code} error")
        error.response = self
        raise error


@pytest.mark.parametrize("payload", [{}, {"jobs": None}, {"jobs": ["bad"]}])
def test_require_job_list_rejects_malformed_board_payload(payload):
    with pytest.raises(_http.IncompleteBoardResponse):
        _http.require_job_list(payload, "greenhouse", "acme", key="jobs")


def test_require_job_list_accepts_a_confirmed_empty_board():
    assert _http.require_job_list({"jobs": []}, "greenhouse", "acme", key="jobs") == []


def test_get_with_retries_returns_response_on_first_success(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse({"ok": True})

    monkeypatch.setattr(_http._session, "get", fake_get)

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
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr(_http._session, "get", fake_get)

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
        return FakeResponse({"ok": True})

    monkeypatch.setattr(_http._session, "get", fake_get)
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
        return FakeResponse({"ok": True})

    monkeypatch.setattr(_http._session, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    response = _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == 2
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("status_code, retry_after, expected", [
    (429, "7", 7.0),
    (429, "3600", _http.MAX_RETRY_AFTER_SECONDS),
    (429, "Wed, 21 Oct 2026 07:28:00 GMT", _http.BACKOFF_SECONDS),
    (429, "nan", _http.BACKOFF_SECONDS),
    (503, "7", _http.BACKOFF_SECONDS),
])
def test_get_with_retries_honors_a_429_retry_after_in_seconds(
    monkeypatch, status_code, retry_after, expected
):
    responses = [
        _RespondingWith(status_code, {"Retry-After": retry_after}),
        FakeResponse({"ok": True}),
    ]
    sleeps = []
    monkeypatch.setattr(_http._session, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(_http.time, "sleep", sleeps.append)

    _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert sleeps == [expected]


def test_board_requests_share_one_session_with_a_user_agent():
    assert _http._session.headers["User-Agent"] == _http.USER_AGENT


def test_get_with_retries_gives_up_after_max_attempts_on_5xx(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(503)

    monkeypatch.setattr(_http._session, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    with pytest.raises(_http.requests.HTTPError):
        _http.get_with_retries("greenhouse", "acme", "https://x/jobs")

    assert len(calls) == _http.MAX_ATTEMPTS


def test_get_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _RespondingWith(404)

    monkeypatch.setattr(_http._session, "get", fake_get)
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
    monkeypatch.setattr(_http._session, "get", fake_get)
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
        return FakeResponse()

    monkeypatch.setattr(_http._session, "get", fake_get)
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
        return FakeResponse({"ok": True})

    monkeypatch.setattr(_http._session, "post", fake_post)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    response = _http.post_with_retries("apple", "SFTWR", "https://x/jobs", json={"page": 1})

    assert response.json() == {"ok": True}
    assert calls == [("https://x/jobs", {"page": 1}, 30)] * 2


def test_post_with_retries_does_not_retry_a_client_error(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return _RespondingWith(400)

    monkeypatch.setattr(_http._session, "post", fake_post)
    monkeypatch.setattr(
        _http.time, "sleep", lambda seconds: pytest.fail("should not retry a 400")
    )

    with pytest.raises(_http.requests.HTTPError):
        _http.post_with_retries("apple", "SFTWR", "https://x/jobs", json={})

    assert len(calls) == 1


def test_paginate_with_retries_keeps_the_first_total_when_later_pages_report_zero(
    monkeypatch,
):
    # A listing may report its real total only on the first page and 0 on
    # every page after it -- the loop must keep going on the first total.
    pages = {
        0: {"total": 5, "items": [{"id": 0}, {"id": 1}]},
        2: {"total": 0, "items": [{"id": 2}, {"id": 3}]},
        4: {"total": 0, "items": [{"id": 4}]},
    }
    offsets = []

    def fake_get(url, params=None, timeout=None):
        offsets.append(params["offset"])
        return FakeResponse(pages[params["offset"]])

    monkeypatch.setattr(_http._session, "get", fake_get)

    items = _http.paginate_with_retries(
        "test", "acme", "https://x/jobs",
        cursor_param="offset", page_size_param="limit", page_size=2,
        total_key="total", items_key="items",
    )

    assert [item["id"] for item in items] == [0, 1, 2, 3, 4]
    assert offsets == [0, 2, 4]


def test_paginate_with_retries_gets_cursor_as_query_params(monkeypatch):
    params_seen = []

    def fake_get(url, params=None, timeout=None):
        params_seen.append(params)
        if params["offset"] == 0:
            return FakeResponse({"totalFound": 3, "content": [{"id": 0}, {"id": 1}]})
        return FakeResponse({"totalFound": 3, "content": [{"id": 2}]})

    monkeypatch.setattr(_http._session, "get", fake_get)

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

    assert [item["id"] for item in items] == [0, 1, 2]
    assert params_seen == [{"offset": 0, "limit": 2}, {"offset": 2, "limit": 2}]


def _get_pages(monkeypatch, page_for_call):
    """Fake requests.get: page_for_call(offset, nth_call_for_that_offset)
    returns the payload (or a _RespondingWith) for each request."""
    calls: dict[int, int] = {}

    def fake_get(url, params=None, timeout=None):
        offset = params["offset"]
        calls[offset] = calls.get(offset, 0) + 1
        result = page_for_call(offset, calls[offset])
        return result if isinstance(result, _RespondingWith) else FakeResponse(result)

    monkeypatch.setattr(_http._session, "get", fake_get)
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)
    return calls


def _paginate(workers=1, page_size=2):
    return _http.paginate_with_retries(
        "test", "acme", "https://x/jobs",
        cursor_param="offset", page_size_param="limit", page_size=page_size,
        total_key="total", items_key="items", workers=workers,
    )


def _full_page(offset, total=5, size=2):
    return {
        "total": total,
        "items": [{"id": n} for n in range(offset, min(offset + size, total))],
    }


def test_paginate_with_retries_in_parallel_fetches_every_page_once_in_order(monkeypatch):
    calls = _get_pages(monkeypatch, lambda offset, nth: _full_page(offset))

    items = _paginate(workers=4)

    assert [item["id"] for item in items] == [0, 1, 2, 3, 4]
    assert calls == {0: 1, 2: 1, 4: 1}


def test_paginate_with_retries_in_parallel_falls_back_when_a_page_fails(
    monkeypatch, caplog
):
    # Offset 2 fails every attempt of the parallel pass (MAX_ATTEMPTS 503s),
    # then works when the one-page-at-a-time fallback asks again.
    def page_for_call(offset, nth):
        if offset == 2 and nth <= _http.MAX_ATTEMPTS:
            return _RespondingWith(503)
        return _full_page(offset)

    calls = _get_pages(monkeypatch, page_for_call)

    with caplog.at_level("WARNING"):
        items = _paginate(workers=4)

    assert [item["id"] for item in items] == [0, 1, 2, 3, 4]
    assert "parallel page fetch failed" in caplog.text
    assert calls[0] == 2  # once per pass -- the fallback starts over


def test_paginate_with_retries_in_parallel_falls_back_when_pages_come_back_short(
    monkeypatch, caplog
):
    # Offset 2 comes back one item short the first time (e.g. a posting
    # closed mid-fetch), so the parallel pass doesn't add up to its total.
    def page_for_call(offset, nth):
        page = _full_page(offset)
        if offset == 2 and nth == 1:
            page["items"] = page["items"][:1]
        return page

    _get_pages(monkeypatch, page_for_call)

    with caplog.at_level("WARNING"):
        items = _paginate(workers=4)

    assert [item["id"] for item in items] == [0, 1, 2, 3, 4]
    assert "different number of items" in caplog.text


def test_paginate_with_retries_in_parallel_stops_after_a_single_full_page(monkeypatch):
    calls = _get_pages(monkeypatch, lambda offset, nth: _full_page(offset, total=2))

    items = _paginate(workers=4)

    assert [item["id"] for item in items] == [0, 1]
    assert calls == {0: 1}


def test_paginate_with_retries_retries_an_incomplete_sequential_snapshot(monkeypatch):
    def page_for_call(offset, nth):
        if offset == 2 and nth == 1:
            return {"total": 5, "items": []}
        return _full_page(offset)

    calls = _get_pages(monkeypatch, page_for_call)
    items = _paginate()

    assert [item["id"] for item in items] == [0, 1, 2, 3, 4]
    assert calls == {0: 2, 2: 2, 4: 1}


def test_paginate_with_retries_rejects_a_missing_total(monkeypatch):
    calls = _get_pages(monkeypatch, lambda offset, nth: {"items": [{"id": 0}]})

    with pytest.raises(_http.IncompleteBoardResponse, match="total"):
        _paginate()

    assert calls == {0: 2}


@pytest.mark.parametrize("workers", [1, 4])
def test_paginate_rejects_duplicate_ids_even_when_totals_match(monkeypatch, workers):
    calls = _get_pages(monkeypatch, lambda offset, nth: {
        "total": 4,
        "items": [{"id": 1}, {"id": 2}] if offset == 0 else [{"id": 2}, {"id": 3}],
    })
    with pytest.raises(_http.IncompleteBoardResponse, match="duplicate"):
        _paginate(workers=workers)
    assert calls[0] >= 2  # The whole snapshot was retried before rejecting it.


def test_paginate_recovers_when_retry_has_unique_ids(monkeypatch):
    def page(offset, nth):
        ids = [1, 2] if offset == 0 else ([2, 3] if nth == 1 else [3, 4])
        return {"total": 4, "items": [{"id": value} for value in ids]}

    _get_pages(monkeypatch, page)
    items = _paginate()
    assert [item["id"] for item in items] == [1, 2, 3, 4]


def test_paginate_rejects_an_item_without_an_id(monkeypatch):
    _get_pages(monkeypatch, lambda offset, nth: {
        "total": 2, "items": [{"id": 1}, {"title": "Engineer"}],
    })

    with pytest.raises(_http.IncompleteBoardResponse, match="missing or invalid id"):
        _paginate()


@pytest.mark.parametrize("overlap", [0, -1, 4, 5])
def test_paginate_with_overlap_rejects_an_overlap_that_cant_advance(overlap):
    # A page must repeat at least one item and still move the cursor forward.
    with pytest.raises(ValueError, match="overlap"):
        _http.paginate_with_overlap(
            "test", "board", "https://x", cursor_param="offset",
            page_size_param="limit", page_size=4, overlap=overlap,
            total_key="total", items_key="items",
        )


class _NumberedListing:
    """A fake endpoint paged by number; each page serves its payloads in order.

    A page's last payload repeats once the earlier ones are used up.
    """

    def __init__(self, payloads_by_page):
        self._payloads = {page: list(payloads) for page, payloads in payloads_by_page.items()}
        self.calls = []

    def __call__(self, page):
        self.calls.append(page)
        payloads = self._payloads[page]
        return payloads.pop(0) if len(payloads) > 1 else payloads[0]


def _numbered_page(ids, total):
    return {"total": total, "items": [{"id": item_id} for item_id in ids]}


def _paginate_numbered(listing, **kwargs):
    return _http.paginate_numbered_pages(
        "test", "board", listing, page_size=2, total_key="total", items_key="items",
        **kwargs,
    )


def _item_ids(items):
    return [item["id"] for item in items]


@pytest.mark.parametrize("workers", [1, 3])
def test_paginate_numbered_pages_fetches_each_page_then_rechecks_the_first(workers):
    listing = _NumberedListing({
        1: [_numbered_page("ab", 5)],
        2: [_numbered_page("cd", 5)],
        3: [_numbered_page("e", 5)],
    })

    assert _item_ids(_paginate_numbered(listing, workers=workers)) == list("abcde")
    assert sorted(listing.calls[1:3]) == [2, 3]
    assert listing.calls[0] == listing.calls[-1] == 1
    assert len(listing.calls) == 4


def test_paginate_numbered_pages_skips_the_recheck_for_a_single_page():
    listing = _NumberedListing({1: [_numbered_page("ab", 2)]})

    assert _item_ids(_paginate_numbered(listing)) == ["a", "b"]
    assert listing.calls == [1]


def test_paginate_numbered_pages_refetches_pages_around_a_repeated_item():
    # "b" and "c" tie, so "b" shows on both pages and "c" on neither; the
    # page lengths still add up to the total.
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4), _numbered_page("ac", 4)],
        2: [_numbered_page("bd", 4), _numbered_page("bd", 4)],
    })

    assert sorted(_item_ids(_paginate_numbered(listing))) == list("abcd")
    assert listing.calls[:2] == [1, 2]
    assert sorted(listing.calls[2:4]) == [1, 2]


def test_paginate_numbered_pages_fails_when_repairs_never_reach_the_total():
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4)],
        2: [_numbered_page("bd", 4)],
    })

    with pytest.raises(_http.IncompleteBoardResponse, match="3 distinct item"):
        _paginate_numbered(listing, repair_rounds=2)
    # Two passes of the whole board, each with two rounds of repairs.
    assert len(listing.calls) == 2 * (2 + 2 * 2)


def test_paginate_numbered_pages_refetches_when_merged_items_pass_the_total(caplog):
    # A refetched page brings "x" and "y", never seen, while "d" closed: the
    # merged rows no longer describe one listing, so the board is refetched.
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4), _numbered_page("ax", 4)],
        2: [_numbered_page("bd", 4), _numbered_page("by", 4)],
    })

    assert _item_ids(_paginate_numbered(listing)) == list("axby")
    assert "5 distinct item(s)" in caplog.text


def test_paginate_numbered_pages_retries_a_total_that_changes_mid_fetch():
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4), _numbered_page("ab", 3)],
        2: [_numbered_page("cd", 5), _numbered_page("c", 3)],
    })

    assert _item_ids(_paginate_numbered(listing)) == list("abc")


def test_paginate_numbered_pages_fails_a_total_that_keeps_changing():
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4)],
        2: [_numbered_page("cd", 5)],
    })

    with pytest.raises(_http.IncompleteBoardResponse, match="total changed"):
        _paginate_numbered(listing)


def test_paginate_numbered_pages_refetches_after_an_item_added_during_the_fetch(caplog):
    # "n" was added at the top after page 1 was read, and "b" closed, so the
    # total and the page lengths still agree; only the recheck notices.
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4), _numbered_page("na", 4)],
        2: [_numbered_page("cd", 4)],
    })

    assert _item_ids(_paginate_numbered(listing)) == list("nacd")
    assert "added during the fetch" in caplog.text


def test_paginate_numbered_pages_fails_when_items_keep_being_added():
    listing = _NumberedListing({
        1: [_numbered_page("ab", 4), _numbered_page("na", 4), _numbered_page("na", 4),
            _numbered_page("mn", 4)],
        2: [_numbered_page("cd", 4)],
    })

    with pytest.raises(_http.IncompleteBoardResponse, match="added during the fetch"):
        _paginate_numbered(listing)


@pytest.mark.parametrize("ids", ["a", "abc"])
def test_paginate_numbered_pages_rejects_a_page_of_the_wrong_length(ids):
    listing = _NumberedListing({
        1: [_numbered_page(ids, 4)],
        2: [_numbered_page("cd", 4)],
    })

    with pytest.raises(_http.IncompleteBoardResponse, match="expected 2"):
        _paginate_numbered(listing)


def test_paginate_numbered_pages_checks_the_first_page_before_any_other():
    listing = _NumberedListing({1: [_numbered_page("ab", 4)], 2: [_numbered_page("cd", 4)]})

    def reject(payload):
        raise _http.IncompleteBoardResponse("rejected")

    with pytest.raises(_http.IncompleteBoardResponse, match="rejected"):
        _paginate_numbered(listing, check_first_page=reject)
    assert listing.calls == [1, 1]  # once per pass, and never page 2


def test_paginate_numbered_pages_rejects_a_duplicate_on_one_page():
    listing = _NumberedListing({1: [_numbered_page("aa", 2)]})

    with pytest.raises(_http.IncompleteBoardResponse, match="duplicate"):
        _paginate_numbered(listing)
