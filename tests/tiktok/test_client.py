"""Tests for tiktok.client using fake integrations.

Fixture rows are trimmed copies of real lifeattiktok.com search rows
(2026-11-21): one city per posting, with the city's state and country as
its parents.
"""

import datetime

import pytest

from jobscraper.job_boards.common import http
from jobscraper.job_boards.tiktok import client
from tests.fakes import ErrorResponse, FakeResponse

_SCRAPED = datetime.date(2026, 11, 21)

_SINGAPORE = {
    "code": "CT_163",
    "location_type": 3,
    "name": None,
    "en_name": "Singapore",
    "i18n_name": "Singapore",
    "parent": {
        "code": "ST_100785",
        "location_type": 2,
        "en_name": "Singapore",
        "parent": {"code": "CN_25", "location_type": 1, "en_name": "Singapore", "parent": None},
    },
}

_JAKARTA = {
    "code": "CT_94",
    "location_type": 3,
    "en_name": "Jakarta",
    "parent": {
        "code": "ST_100593",
        "location_type": 2,
        "en_name": "DKI Jakarta",
        "parent": {"code": "CN_169", "location_type": 1, "en_name": "Indonesia", "parent": None},
    },
}


def _row(post_id, title, city=_SINGAPORE, **fields):
    return {
        "id": post_id,
        "code": "A141466A",
        "title": title,
        "city_info": city,
        "job_category": {
            "id": "6704216109274368264",
            "en_name": "Backend",
            "parent": {"id": "6704215862603155720", "en_name": "R&D", "parent": None},
        },
        "recruit_type": {"id": "101", "en_name": "Regular"},
        "job_subject": None,
        "description": "About the team\nTikTok Shop is a content e-Commerce business.",
        "requirement": "Minimum Qualifications:\n- Bachelor's degree in Computer Science.",
        "job_post_info": {"min_salary": None, "max_salary": None, "currency": None},
        "department_info": None,
        "tag_list": None,
        **fields,
    }


def _serve(monkeypatch, rows, count=None, code=0):
    """Answer the search POST with `rows`; return the requests it received."""
    requests = []

    def fake_post(url, json=None, timeout=None, headers=None):
        requests.append((url, json, headers))
        return FakeResponse({
            "code": code,
            "message": "ok" if code == 0 else "invalid params",
            "data": {
                "job_post_list": rows,
                "count": len(rows) if count is None else count,
            },
        })

    monkeypatch.setattr(http._session, "post", fake_post)
    return requests


def _fetch(token="CT_163"):
    return client.fetch_tiktok_jobs(
        token, "TikTok", "Consumer Comms/Social", scraped_date=_SCRAPED
    )


def test_fetch_tiktok_jobs_parses_a_posting(monkeypatch):
    _serve(monkeypatch, [_row("7670049572916267317", "Backend Software Engineer - TikTok Shop")])

    [job] = _fetch()

    assert job.board == "TikTok"
    assert job.job_id == "7670049572916267317"
    assert job.title == "Backend Software Engineer - TikTok Shop"
    assert job.location == "Singapore"
    assert job.url == "https://lifeattiktok.com/search/7670049572916267317"
    assert job.department == "Backend"
    assert job.source == "TikTok"
    assert job.company_type == "Consumer Comms/Social"
    assert job.scraped_date == _SCRAPED
    # 7670049572916267317 >> 32 is 1785822578, 2026-08-04 05:49:38 UTC.
    assert job.first_published == "2026-08-04"
    # The listing has no update date, and no description is kept for this firm.
    assert (job.updated_at, job.description) == ("", "")


def test_fetch_tiktok_jobs_requests_the_tokens_cities_in_one_page(monkeypatch):
    requests = _serve(monkeypatch, [])

    assert _fetch("CT_163,CT_94") == []

    [(url, body, headers)] = requests
    assert url == client._SEARCH_URL
    assert body["location_code_list"] == ["CT_163", "CT_94"]
    assert (body["offset"], body["limit"]) == (0, client._LIMIT)
    # Without this header the endpoint answers HTTP 400.
    assert headers == {"website-path": "tiktok"}


@pytest.mark.parametrize(
    "post_id",
    [
        "1",  # decodes to 1970, before ByteDance existed
        str((int(datetime.datetime(2026, 11, 25, tzinfo=datetime.timezone.utc).timestamp()) << 32) + 5),
        "R-7",
    ],
)
def test_fetch_tiktok_jobs_leaves_an_implausible_id_date_blank(monkeypatch, post_id):
    _serve(monkeypatch, [_row(post_id, "Data Engineer")])

    [job] = _fetch()

    assert job.first_published == ""


def test_fetch_tiktok_jobs_accepts_an_id_created_on_the_scrape_date(monkeypatch):
    created = datetime.datetime(2026, 11, 21, 23, 59, tzinfo=datetime.timezone.utc)
    _serve(monkeypatch, [_row(str(int(created.timestamp()) << 32), "Data Engineer")])

    [job] = _fetch()

    assert job.first_published == "2026-11-21"


def test_fetch_tiktok_jobs_names_a_city_with_its_country(monkeypatch):
    _serve(monkeypatch, [_row("1", "Data Engineer", city=_JAKARTA)])

    [job] = _fetch("CT_94")

    assert job.location == "Jakarta, Indonesia"


@pytest.mark.parametrize(
    ("recruit_type", "subject", "seniority"),
    [
        ("Intern", "Undergraduate/Master Intern - 2027 Start", "Intern"),
        ("Regular", "Bachelor/Master Graduate - 2027 Start", "Graduate"),
        ("Regular", None, "Unspecified"),
    ],
)
def test_fetch_tiktok_jobs_falls_back_to_the_recruit_type_for_seniority(
    monkeypatch, recruit_type, subject, seniority
):
    _serve(monkeypatch, [_row(
        "1",
        "Backend Engineer (Global Payment)",
        recruit_type={"id": "202", "en_name": recruit_type},
        job_subject={"id": "7602165701485299973", "en_name": subject} if subject else None,
    )])

    [job] = _fetch()

    assert job.seniority == seniority


def test_fetch_tiktok_jobs_prefers_the_titles_level(monkeypatch):
    _serve(monkeypatch, [_row(
        "1", "Senior Backend Engineer", recruit_type={"id": "202", "en_name": "Intern"}
    )])

    [job] = _fetch()

    assert job.seniority == "Senior"


def test_fetch_tiktok_jobs_drops_a_posting_outside_the_tokens_cities(monkeypatch):
    _serve(monkeypatch, [_row("1", "Data Engineer"), _row("2", "Data Engineer", city=_JAKARTA)])

    assert [job.job_id for job in _fetch()] == ["1"]


def test_fetch_tiktok_jobs_fails_when_the_list_is_short_of_its_count(monkeypatch):
    _serve(monkeypatch, [_row("1", "Data Engineer")], count=2)

    with pytest.raises(http.IncompleteBoardResponse, match="1 posting.*count 2"):
        _fetch()


@pytest.mark.parametrize("count", [None, "1", True])
def test_fetch_tiktok_jobs_fails_without_a_numeric_count(monkeypatch, count):
    def fake_post(url, json=None, timeout=None, headers=None):
        data = {"job_post_list": [_row("1", "Data Engineer")]}
        if count is not None:
            data["count"] = count
        return FakeResponse({"code": 0, "message": "ok", "data": data})

    monkeypatch.setattr(http._session, "post", fake_post)

    with pytest.raises(http.IncompleteBoardResponse, match="count"):
        _fetch()


def test_fetch_tiktok_jobs_fails_on_an_error_code(monkeypatch):
    _serve(monkeypatch, [], code=1001)

    with pytest.raises(http.IncompleteBoardResponse, match="code 1001"):
        _fetch()


@pytest.mark.parametrize(
    "rows",
    [
        [_row("1", "Data Engineer"), _row("1", "Data Engineer")],
        [_row(None, "Data Engineer")],
    ],
)
def test_fetch_tiktok_jobs_rejects_a_missing_or_duplicate_id(monkeypatch, rows):
    _serve(monkeypatch, rows)

    with pytest.raises(http.IncompleteBoardResponse, match="id"):
        _fetch()


def test_fetch_tiktok_jobs_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(http._session, "post", lambda *a, **k: ErrorResponse(400))

    with pytest.raises(http.requests.HTTPError):
        _fetch()


def test_fetch_tiktok_jobs_keeps_the_description_for_an_allowlisted_firm(monkeypatch):
    from jobscraper.job_boards.common import jobs as common_jobs

    monkeypatch.setattr(common_jobs, "FIRMS_NEEDING_DESCRIPTION", frozenset({"TikTok"}))
    _serve(monkeypatch, [_row("1", "Data Engineer")])

    [job] = _fetch()

    assert job.description == (
        "About the team\nTikTok Shop is a content e-Commerce business.\n\n"
        "Minimum Qualifications:\n- Bachelor's degree in Computer Science."
    )
