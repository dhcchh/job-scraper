"""Tests for workday.client -- JSON-to-Job parsing, no real HTTP calls.

Mirrors tests/eightfold/test_client.py's structure, adapted to Workday's
response shape (confirmed against Visa's and Mastercard's live boards): a
POSTed `{"total", "jobPostings": [...]}` search paginated via `offset`/
`limit` where only the first page carries a real `total`, relative
"Posted N Days Ago" dates, and multi-office postings whose offices only come
from a per-posting detail GET.
"""

import datetime

import pytest

from jobscraper.job_boards import _http
from jobscraper.job_boards.workday import client

_SCRAPED = datetime.date(2026, 9, 26)


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Fail any test that reaches the network without mocking it first --
    each test's own monkeypatch of _http.requests.get/post replaces these."""

    def fail(*args, **kwargs):
        pytest.fail(f"unmocked HTTP call: {args!r}")

    monkeypatch.setattr(_http.requests, "get", fail)
    monkeypatch.setattr(_http.requests, "post", fail)
_API = "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _NotFound:
    status_code = 404

    def raise_for_status(self):
        error = _http.requests.HTTPError("404")
        error.response = self
        raise error


def _posting(path, title="Software Engineer", locations_text="SG - Singapore", posted_on="Posted Today"):
    raw = {"title": title, "externalPath": path, "postedOn": posted_on}
    if locations_text is not None:
        raw["locationsText"] = locations_text
    return raw


def _serve(monkeypatch, postings, details=None, calibrate=False):
    """Answer the list POST with `postings` (one page) and each detail GET
    from `details` (externalPath -> jobPostingInfo, or a response object).

    `calibrate=False` skips the one detail call client._workday_today makes
    to learn Workday's own date, so "Posted N Days Ago" counts back from
    `scraped_date` -- tests about that call pass True and serve its detail.
    """
    posts = []
    gets = []
    if not calibrate:
        monkeypatch.setattr(client, "_workday_today", lambda token, api, raw_jobs: None)

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        page = postings if json["offset"] == 0 else []
        return _FakeResponse({"total": len(postings), "jobPostings": page})

    def fake_get(url, params=None, timeout=None):
        gets.append(url)
        detail = (details or {})[url[len(_API):]]
        if not isinstance(detail, dict):
            return detail
        return _FakeResponse({"jobPostingInfo": detail})

    monkeypatch.setattr(_http.requests, "post", fake_post)
    monkeypatch.setattr(_http.requests, "get", fake_get)
    return posts, gets


def test_fetch_workday_jobs_parses_response(monkeypatch):
    posts, gets = _serve(
        monkeypatch,
        [
            _posting(
                "/job/SG---Singapore/Senior-Software-Engineer_REF088713W",
                title="Senior Software Engineer",
                posted_on="Posted Yesterday",
            ),
            _posting(
                "/job/US---Austin-TX/Data-Scientist_REF1-1",
                title="Data Scientist",
                locations_text="US - Austin, TX",
                posted_on="Posted 30+ Days Ago",
            ),
        ],
    )

    jobs = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert posts[0] == (
        f"{_API}/jobs",
        {"appliedFacets": {}, "searchText": "", "offset": 0, "limit": client._PAGE_SIZE},
    )
    assert gets == []  # single-office postings never need a detail call
    assert len(jobs) == 2

    first, second = jobs
    assert first.board == "Visa"
    assert first.job_id == "REF088713W"
    assert first.title == "Senior Software Engineer"
    assert first.department == ""
    assert first.location == "Singapore"
    assert first.url == (
        "https://visa.wd5.myworkdayjobs.com/Visa"
        "/job/SG---Singapore/Senior-Software-Engineer_REF088713W"
    )
    assert first.first_published == "2026-09-25"  # "Posted Yesterday"
    assert first.updated_at == ""
    assert first.scraped_date == _SCRAPED
    assert first.seniority == "Senior"
    assert first.company_type == "Fintech"
    assert first.source == "Workday"
    assert first.description == ""
    assert first.salary is None

    assert second.job_id == "REF1-1"  # repost suffix kept -- it's a new posting
    assert second.location == "Austin, TX"
    assert second.first_published == ""  # "30+ Days Ago" isn't a date


def test_fetch_workday_jobs_searches_and_strictly_filters_a_location_scope(
    monkeypatch,
):
    multi = "/job/x/Data-Scientist_R-3"
    posts, gets = _serve(
        monkeypatch,
        [
            _posting(
                "/job/x/Software-Engineer_R-1",
                locations_text="Singapore - Singapore",
            ),
            # Workday's full-text search can match the JD even when the job
            # itself is elsewhere; the client must remove this false positive.
            _posting(
                "/job/x/Software-Engineer_R-2",
                locations_text="India - Hyderabad",
            ),
            _posting(multi, title="Data Scientist", locations_text="2 Locations"),
        ],
        details={
            multi: {
                "location": "Singapore, Pasir Panjang",
                "additionalLocations": ["Hong Kong"],
            }
        },
    )

    jobs = client.fetch_workday_jobs(
        "visa.wd5/Visa",
        "Visa",
        "Fintech",
        location_scope="Singapore",
        scraped_date=_SCRAPED,
    )

    assert posts[0][1]["searchText"] == "Singapore"
    assert gets == [f"{_API}{multi}"]
    assert [(job.job_id, job.location) for job in jobs] == [
        ("R-1", "Singapore"),
        ("R-3", "Singapore | Hong Kong"),
    ]


def test_fetch_workday_jobs_counts_days_back_from_workdays_own_date(monkeypatch):
    # Workday says "Posted 2 Days Ago" for a posting whose exact startDate is
    # 2026-09-20, so its own date is 2026-09-22 -- every relative date counts
    # back from that, not from this machine's (later) scrape date.
    first = "/job/a/Software-Engineer_R-1"
    _posts, gets = _serve(
        monkeypatch,
        [
            _posting("/job/z/Old-Role_R-0", posted_on="Posted 30+ Days Ago"),
            _posting(first, posted_on="Posted 2 Days Ago"),
            _posting("/job/b/Data-Scientist_R-2", posted_on="Posted Yesterday"),
        ],
        details={first: {"startDate": "2026-09-20", "postedOn": "Posted 2 Days Ago"}},
        calibrate=True,
    )

    old, a, b = client.fetch_workday_jobs(
        "visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED
    )

    assert gets == [f"{_API}{first}"]  # skips the "30+ Days Ago" posting
    assert old.first_published == ""
    assert a.first_published == "2026-09-20"
    assert b.first_published == "2026-09-21"
    assert b.scraped_date == _SCRAPED  # scraped_date itself is unchanged


def test_fetch_workday_jobs_falls_back_to_scraped_date_if_workdays_date_is_unknown(
    monkeypatch, caplog
):
    path = "/job/a/Software-Engineer_R-1"
    _serve(
        monkeypatch,
        [_posting(path, posted_on="Posted Yesterday")],
        details={path: _NotFound()},
        calibrate=True,
    )

    with caplog.at_level("WARNING"):
        [job] = client.fetch_workday_jobs(
            "visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED
        )

    assert job.first_published == "2026-09-25"
    assert "couldn't read Workday's current date" in caplog.text


@pytest.mark.parametrize(
    "posted_on, expected",
    [
        ("Posted Today", "2026-09-26"),
        ("Posted Yesterday", "2026-09-25"),
        ("Posted 2 Days Ago", "2026-09-24"),
        ("Posted 30 Days Ago", "2026-08-27"),
        ("Posted 30+ Days Ago", ""),
        ("", ""),
        (None, ""),
        ("Posted last week", ""),
    ],
)
def test_posted_on_date(posted_on, expected):
    assert client._posted_on_date(posted_on, _SCRAPED) == expected


@pytest.mark.parametrize(
    "token, host, api",
    [
        (
            "visa.wd5/Visa",
            "visa.wd5.myworkdayjobs.com",
            "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa",
        ),
        (
            "mastercard.wd1/CorporateCareers",
            "mastercard.wd1.myworkdayjobs.com",
            "https://mastercard.wd1.myworkdayjobs.com/wday/cxs/mastercard/CorporateCareers",
        ),
    ],
)
def test_parse_token(token, host, api):
    assert client._parse_token(token) == (host, api)


@pytest.mark.parametrize(
    "token", ["visa", "visa/Visa", "visa.wd5", "https://visa.wd5.myworkdayjobs.com/Visa"]
)
def test_parse_token_rejects_a_malformed_token(token):
    with pytest.raises(ValueError, match="<tenant>.<wdN>/<site>"):
        client._parse_token(token)


def test_fetch_workday_jobs_keeps_paging_past_a_zero_total(monkeypatch):
    # Workday only reports `total` on the first page -- later pages say 0.
    pages = {
        0: {"total": 3, "jobPostings": [_posting("/job/a_R-1"), _posting("/job/b_R-2")]},
        2: {"total": 0, "jobPostings": [_posting("/job/c_R-3")]},
    }

    def fake_post(url, json=None, timeout=None):
        return _FakeResponse(pages[json["offset"]])

    monkeypatch.setattr(client, "_PAGE_SIZE", 2)
    monkeypatch.setattr(_http.requests, "post", fake_post)
    monkeypatch.setattr(client, "_workday_today", lambda token, api, raw_jobs: None)

    jobs = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert [job.job_id for job in jobs] == ["R-1", "R-2", "R-3"]


@pytest.mark.parametrize("workers", [client._WORKERS, 1])
def test_fetch_workday_jobs_fetches_detail_for_a_multi_office_category_posting(
    monkeypatch, workers
):
    # workers=1 is the one-request-at-a-time setting kept as a fallback.
    monkeypatch.setattr(client, "_WORKERS", workers)
    path = "/job/US---Foster-City-CA/Software-Engineer_REF2"
    _posts, gets = _serve(
        monkeypatch,
        [_posting(path, locations_text="3 Locations", posted_on="Posted 30+ Days Ago")],
        details={
            path: {
                "location": "US - Foster City, CA",
                "additionalLocations": ["SG - Singapore", "US - Foster City, CA"],
                "startDate": "2026-08-01",
            }
        },
    )

    [job] = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert gets == [f"{_API}{path}"]
    assert job.location == "Foster City, CA | Singapore"
    assert job.first_published == "2026-08-01"  # exact, from startDate


def test_fetch_workday_jobs_fetches_detail_for_a_posting_with_no_location(monkeypatch):
    path = "/job/Principal-Software-Engineer_R-267932"
    _posts, gets = _serve(
        monkeypatch,
        [_posting(path, locations_text=None)],
        details={path: {"location": "Singapore", "startDate": "2026-09-20"}},
    )

    [job] = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert len(gets) == 1
    assert job.location == "Singapore"


def test_fetch_workday_jobs_falls_back_to_the_requisition_location(monkeypatch):
    # One live Mastercard posting has no posting location at all, only its
    # requisition's.
    path = "/job/Principal-Software-Engineer-TS_R-267932"
    _serve(
        monkeypatch,
        [_posting(path, locations_text=None)],
        details={
            path: {
                "location": "",
                "additionalLocations": None,
                "jobRequisitionLocation": {"descriptor": "Pune, India"},
            }
        },
    )

    [job] = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert job.location == "Pune, India"


def test_fetch_workday_jobs_skips_detail_for_a_multi_office_noise_posting(monkeypatch):
    # A title no category matches is dropped by main.py before its location
    # is ever read, so it isn't worth a ~1-4s detail call.
    _posts, gets = _serve(
        monkeypatch,
        [_posting("/job/x/Account-Executive_R-9", title="Account Executive", locations_text="2 Locations")],
    )

    [job] = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert gets == []
    assert job.location == ""


def test_fetch_workday_jobs_drops_only_the_posting_whose_detail_fails(monkeypatch):
    closed = "/job/x/Software-Engineer_R-7"
    _serve(
        monkeypatch,
        [
            _posting(closed, locations_text="2 Locations"),
            _posting("/job/y/Software-Engineer_R-8"),
        ],
        details={closed: _NotFound()},
    )

    jobs = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert [job.job_id for job in jobs] == ["R-8"]


def test_fetch_workday_jobs_retries_a_failed_detail_call_once_more_before_dropping(
    monkeypatch,
):
    # The parallel pass's detail call keeps getting 503s (every retry), but
    # the one-at-a-time retry afterward works -- the posting is kept.
    path = "/job/x/Software-Engineer_R-7"
    attempts = []

    class _Unavailable:
        status_code = 503

        def raise_for_status(self):
            error = _http.requests.HTTPError("503")
            error.response = self
            raise error

    def detail(url):
        attempts.append(url)
        if len(attempts) <= _http.MAX_ATTEMPTS:
            return _Unavailable()
        return _FakeResponse({"jobPostingInfo": {"location": "SG - Singapore"}})

    _serve(monkeypatch, [_posting(path, locations_text="2 Locations")])
    monkeypatch.setattr(_http.requests, "get", lambda url, params=None, timeout=None: detail(url))
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    [job] = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert job.location == "Singapore"
    assert len(attempts) == _http.MAX_ATTEMPTS + 1


def test_fetch_workday_jobs_fails_board_on_persistent_detail_server_error(monkeypatch):
    path = "/job/x/Software-Engineer_R-7"

    class _Unavailable:
        status_code = 503

        def raise_for_status(self):
            error = _http.requests.HTTPError("503")
            error.response = self
            raise error

    _posts, gets = _serve(
        monkeypatch,
        [_posting(path, locations_text="2 Locations")],
        details={path: _Unavailable()},
    )
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="unsafe to prune"):
        client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert len(gets) == 2 * _http.MAX_ATTEMPTS


def test_fetch_workday_jobs_fails_board_on_missing_detail_payload(monkeypatch):
    path = "/job/x/Software-Engineer_R-7"
    _posts, gets = _serve(
        monkeypatch,
        [_posting(path, locations_text="2 Locations")],
        details={path: _FakeResponse({})},
    )

    with pytest.raises(RuntimeError, match="unsafe to prune"):
        client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert len(gets) == 2


def test_fetch_workday_jobs_skips_a_missing_or_duplicate_external_path(monkeypatch):
    _serve(
        monkeypatch,
        [
            {"title": "No path"},
            _posting("/job/a/Software-Engineer_R-1"),
            _posting("/job/a/Software-Engineer_R-1"),
        ],
    )

    jobs = client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech", scraped_date=_SCRAPED)

    assert [job.job_id for job in jobs] == ["R-1"]


def test_fetch_workday_jobs_raises_on_http_error(monkeypatch):
    # A persistent list-page failure must propagate out of fetch_workday_jobs
    # -- main.py is what decides that means "skip this board", not this
    # client. The retry/backoff policy itself is job_boards._http's own
    # behavior -- see tests/job_boards/test_http.py, not duplicated here.
    class _FailingResponse:
        def raise_for_status(self):
            raise _http.requests.HTTPError("boom")

    monkeypatch.setattr(_http.requests, "post", lambda *a, **k: _FailingResponse())

    with pytest.raises(_http.requests.HTTPError):
        client.fetch_workday_jobs("visa.wd5/Visa", "Visa", "Fintech")
