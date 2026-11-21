"""Tests for apple.client using fake integrations.

Fixture rows are trimmed copies of real jobs.apple.com /api/v1/search rows
(2026-11-16): one row per office, sharing `positionId`.
"""

import datetime

import pytest

from jobscraper.enrichment.classify_region import classify_region, is_excluded
from jobscraper.job_boards.common import http
from jobscraper.job_boards.common import jobs as common_jobs
from jobscraper.job_boards.apple import client
from tests.fakes import FakeResponse

_TOKEN = "SFTWR,MLAI,STDNT/INTRN"


def _location(name, code, country_name, level=5):
    return {
        "postLocationId": "postLocation-X",
        "city": "",
        "stateProvince": "",
        "countryName": country_name,
        "metro": "",
        "region": "",
        "name": name,
        "countryID": f"iso-country-{code}",
        "level": level,
    }


def _row(position_id, office_code, location, **fields):
    return {
        "id": f"{position_id}-{office_code}",
        "jobSummary": "Are you a software engineer with a passion for forging impactful relationships?",
        "locations": [location],
        "positionId": position_id,
        "postingDate": "Nov 12, 2026",
        "postingTitle": "Senior Full Stack Java Engineer, Enterprise Business Applications",
        "postDateInGMT": "2026-11-12T14:09:33.712Z",
        "transformedPostingTitle": "senior-full-stack-java-engineer-enterprise-business-applications",
        "reqId": f"{position_id}-{office_code}",
        "managedPipelineRole": False,
        "team": {
            "teamName": "Software and Services",
            "teamID": "teamsAndSubTeams-SFTWR",
            "teamCode": "SFTWR",
        },
        "type": "PIPE",
        "homeOffice": False,
        "jobPositionId": f"PIPE-{position_id}",
        "isMultiLocation": False,
        "postExternal": True,
        **fields,
    }


_SINGAPORE = _row("200694689", "3278", _location("Singapore", "SGP", "Singapore"))

# One posting open in three UK offices comes back as three rows.
_UK_ROWS = [
    _row(
        "200646200", code, _location(name, "GBR", "United Kingdom"),
        postingTitle="Experienced CAD Automation Engineer (Design Verification)",
        transformedPostingTitle="experienced-cad-automation-engineer-design-verification",
        postDateInGMT="2026-11-11T11:50:22.678Z",
    )
    for code, name in (("2114", "London"), ("1251", "Cambridge"), ("3521", "Saint Albans"))
]

_INTERN = _row(
    "200692642", "0157", _location("San Francisco Bay Area", "USA", "United States of America", 4),
    postingTitle=" Internship -  Machine Learning Research (recurrent models | generative models |  interpretability)",
    transformedPostingTitle="internship-machine-learning-research",
    team={"teamName": "Students", "teamID": "teamsAndSubTeams-STDNT", "teamCode": "STDNT"},
    type="REQ",
)


def _teams_payload(teams=None):
    teams = teams or {"SFTWR": ["AF", "EPM"], "MLAI": ["CV", "EPPM"], "STDNT": ["INTRN", "CORP"]}
    return {"res": [
        {
            "id": f"teamsAndSubTeams-{team}",
            "type": team,
            "teams": [
                {"id": f"subTeam-{sub_team}", "teamID": f"teamsAndSubTeams-{team}", "code": sub_team}
                for sub_team in sub_teams
            ],
        }
        for team, sub_teams in teams.items()
    ]}


class _Site:
    """A fake jobs.apple.com serving the team list and paged search rows."""

    def __init__(self, monkeypatch, rows, *, teams=None, total=None):
        self.rows = rows
        self.total = total
        self.teams = _teams_payload(teams)
        self.bodies = []
        self.get_urls = []
        monkeypatch.setattr(client, "_PAGE_WORKERS", 1)
        monkeypatch.setattr(http._session, "get", self._get)
        monkeypatch.setattr(http._session, "post", self._post)

    def _get(self, url, params=None, timeout=None):
        self.get_urls.append(url)
        return FakeResponse(self.teams)

    def _post(self, url, json=None, timeout=None):
        assert url == "https://jobs.apple.com/api/v1/search"
        self.bodies.append(json)
        start = (json["page"] - 1) * client._PAGE_SIZE
        total = len(self.rows) if self.total is None else self.total
        return FakeResponse({"res": {
            "searchResults": self.rows[start:start + client._PAGE_SIZE],
            "totalRecords": total,
        }})

    @property
    def pages(self):
        return [body["page"] for body in self.bodies]


def _fetch(token=_TOKEN, firm="Apple"):
    return client.fetch_apple_jobs(
        token, firm, "Consumer/Marketplace", scraped_date=datetime.date(2026, 11, 16)
    )


def test_fetch_apple_jobs_parses_and_groups_rows(monkeypatch):
    site = _Site(monkeypatch, [_SINGAPORE, *_UK_ROWS, _INTERN])

    singapore, uk, intern = _fetch()

    assert site.get_urls == ["https://jobs.apple.com/api/v1/refData/teamsofinterest"]
    assert site.bodies[0] == {
        "query": "",
        "filters": {"teams": [
            {"team": "teamsAndSubTeams-SFTWR"},
            {"team": "teamsAndSubTeams-MLAI"},
            {"team": "teamsAndSubTeams-STDNT", "subTeam": "subTeam-INTRN"},
        ]},
        "page": 1,
        "locale": "en-us",
        "sort": "newest",
        "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
    }

    assert singapore.board == "Apple"
    assert singapore.job_id == "200694689"
    assert singapore.title == "Senior Full Stack Java Engineer, Enterprise Business Applications"
    assert singapore.department == "Software and Services"
    assert singapore.location == "Singapore"
    assert singapore.url == (
        "https://jobs.apple.com/en-us/details/200694689/"
        "senior-full-stack-java-engineer-enterprise-business-applications"
    )
    assert singapore.first_published == "2026-11-12"
    assert singapore.updated_at == ""
    assert singapore.scraped_date == datetime.date(2026, 11, 16)
    assert singapore.company_type == "Consumer/Marketplace"
    assert singapore.source == "Apple"
    assert singapore.seniority == "Senior"
    assert singapore.description == ""  # Apple is not on the allowlist
    assert singapore.salary is None

    # Three office rows make one job, offices in row-ID order.
    assert uk.job_id == "200646200"
    assert uk.location == (
        "Cambridge, United Kingdom | London, United Kingdom | Saint Albans, United Kingdom"
    )
    assert uk.first_published == "2026-11-11"

    # Stray whitespace in a title collapses; a metro-area office keeps its country.
    assert intern.title == (
        "Internship - Machine Learning Research "
        "(recurrent models | generative models | interpretability)"
    )
    assert intern.department == "Students"
    assert intern.location == "San Francisco Bay Area, United States"
    assert intern.seniority == "Intern"


def test_fetch_apple_jobs_requests_every_page_then_rechecks_the_first(monkeypatch):
    rows = [_row(str(200000000 + n), "0001", _location("Cupertino", "USA", "United States of America"))
            for n in range(45)]
    site = _Site(monkeypatch, rows)

    assert len(_fetch()) == 45
    assert site.pages == [1, 2, 3, 1]


def test_fetch_apple_jobs_repairs_offices_split_across_a_page_boundary(monkeypatch):
    # A posting's offices share a timestamp, so they come back in a different
    # order on each request; at a page boundary one office can show on both
    # pages and another on neither.
    monkeypatch.setattr(client, "_PAGE_SIZE", 2)
    a, b, c, d = (
        _row("200000001", "0001", _location("London", "GBR", "United Kingdom")),
        _row("200000002", "0001", _location("London", "GBR", "United Kingdom")),
        _row("200000002", "0002", _location("Cambridge", "GBR", "United Kingdom")),
        _row("200000003", "0001", _location("Cupertino", "USA", "United States of America")),
    )
    site = _Site(monkeypatch, [a, b, b, d])
    orders = [[a, b, b, d], [a, c, b, d]]

    def shuffle_ties(url, json=None, timeout=None):
        if json["page"] == 1 and len(orders) > 1:
            site.rows = orders.pop(0)
        elif json["page"] == 1:
            site.rows = orders[0]
        return _Site._post(site, url, json=json, timeout=timeout)

    monkeypatch.setattr(http._session, "post", shuffle_ties)

    jobs = {job.job_id: job for job in _fetch()}

    assert jobs["200000002"].location == "London, United Kingdom | Cambridge, United Kingdom"
    assert sorted(jobs) == ["200000001", "200000002", "200000003"]
    # Both pages around the repeat are fetched again, then page 1 rechecked.
    assert site.pages[:2] == [1, 2] and sorted(site.pages[2:4]) == [1, 2]
    assert site.pages[4:] == [1]


@pytest.mark.parametrize("token, teams, unknown", [
    ("SFTWR,BOGUS", None, "BOGUS"),
    ("SFTWR,STDNT/INTRNX", None, "STDNT/INTRNX"),
    # A sub-team belongs to its own team only.
    ("SFTWR/CV", None, "SFTWR/CV"),
])
def test_fetch_apple_jobs_rejects_a_team_the_site_doesnt_list(monkeypatch, token, teams, unknown):
    # An unknown code returns no rows, not an error, so it would silently
    # drop its postings from a combined search.
    site = _Site(monkeypatch, [_SINGAPORE], teams=teams)

    with pytest.raises(http.IncompleteBoardResponse, match=f"no team {unknown}$"):
        _fetch(token)
    assert site.bodies == []


@pytest.mark.parametrize("payload", [{}, {"res": None}, {"res": [{"id": "x"}]}, []])
def test_fetch_apple_jobs_rejects_an_unreadable_team_list(monkeypatch, payload):
    site = _Site(monkeypatch, [_SINGAPORE])
    site.teams = payload

    with pytest.raises(http.IncompleteBoardResponse, match="unreadable team list"):
        _fetch()


def test_fetch_apple_jobs_rejects_an_empty_search(monkeypatch):
    # A body the endpoint can't read (no "format") also returns no rows.
    site = _Site(monkeypatch, [])

    with pytest.raises(http.IncompleteBoardResponse, match="no rows"):
        _fetch()
    assert site.pages == [1, 1]  # the whole board is fetched twice


@pytest.mark.parametrize("payload", [{}, {"res": None}, [], {"res": {"totalRecords": 1}}])
def test_fetch_apple_jobs_rejects_a_malformed_search_response(monkeypatch, payload):
    _Site(monkeypatch, [_SINGAPORE])
    monkeypatch.setattr(http._session, "post", lambda *a, **k: FakeResponse(payload))

    with pytest.raises(http.IncompleteBoardResponse):
        _fetch()


def test_fetch_apple_jobs_rejects_a_short_page(monkeypatch):
    _Site(monkeypatch, [_SINGAPORE], total=2)

    with pytest.raises(http.IncompleteBoardResponse, match="expected 2"):
        _fetch()


@pytest.mark.parametrize("field", ["id", "positionId"])
def test_fetch_apple_jobs_rejects_a_row_missing_an_id(monkeypatch, field):
    row = {key: value for key, value in _SINGAPORE.items() if key != field}
    _Site(monkeypatch, [row, _INTERN])

    with pytest.raises(http.IncompleteBoardResponse, match=field):
        _fetch()


def test_fetch_apple_jobs_rejects_a_repeated_row_on_one_page(monkeypatch):
    _Site(monkeypatch, [_SINGAPORE, _SINGAPORE])

    with pytest.raises(http.IncompleteBoardResponse, match="duplicate"):
        _fetch()


def test_fetch_apple_jobs_raises_on_http_error(monkeypatch):
    class _FailingResponse:
        def raise_for_status(self):
            raise http.requests.HTTPError("boom")

    _Site(monkeypatch, [_SINGAPORE])
    monkeypatch.setattr(http._session, "post", lambda *a, **k: _FailingResponse())

    with pytest.raises(http.requests.HTTPError):
        _fetch()


def test_description_is_the_job_summary_for_an_allowlisted_firm(monkeypatch):
    monkeypatch.setattr(common_jobs, "FIRMS_NEEDING_DESCRIPTION", frozenset({"Apple"}))

    job = client._to_job(
        {"positionId": "200694689", "rows": [_SINGAPORE]},
        "Apple", "Consumer/Marketplace", datetime.date(2026, 11, 16),
    )

    assert job.description == (
        "Are you a software engineer with a passion for forging impactful relationships?"
    )


@pytest.mark.parametrize("entry, office, region, excluded", [
    (_location("Hong Kong", "HKG", "Hong Kong"), "Hong Kong", "PAC", False),
    (_location("Shanghai", "CHN", "China"), "Shanghai, China", "PAC", False),
    (_location("Singapore", "SGP", "Singapore"), "Singapore", "PAC", False),
    (_location("Bengaluru", "IND", "India"), "Bangalore, India", "RoW", True),
    # A country-level office, and Apple's own country name.
    (_location("United States", "USA", "United States of America", 1), "United States", "RoW", False),
    # A code missing from the shared table falls back to Apple's country name.
    (_location("Tallinn", "XYZ", "Estonia"), "Tallinn, Estonia", "RoW", False),
    (dict(_location("Tallinn", "EST", "Estonia"), countryID=None), "Tallinn, Estonia", "RoW", False),
])
def test_office_keeps_country_and_region(entry, office, region, excluded):
    assert client._office(entry) == office
    assert classify_region(office) == region
    assert is_excluded(office) == excluded


@pytest.mark.parametrize("locations, expected", [
    ([None, _location("London", "GBR", "United Kingdom")], "London, United Kingdom"),
    ("London", ""),
    (None, ""),
])
def test_location_skips_a_malformed_entry(locations, expected):
    # An office isn't part of a posting's identity, so it can't fail the board.
    assert client._location([dict(_SINGAPORE, locations=locations)]) == expected


def test_location_joins_offices_of_one_posting_without_repeats():
    rows = [
        _row("1", "0001", _location("Singapore", "SGP", "Singapore")),
        _row("1", "0002", _location("Bengaluru", "IND", "India")),
        _row("1", "0003", _location("Singapore", "SGP", "Singapore")),
    ]

    location = client._location(rows)

    assert location == "Singapore | Bangalore, India"
    assert classify_region(location) == "PAC"
    assert not is_excluded(location)


@pytest.mark.parametrize("slug, url", [
    (None, "https://jobs.apple.com/en-us/details/200694689"),
    ("", "https://jobs.apple.com/en-us/details/200694689"),
    ("a/b c", "https://jobs.apple.com/en-us/details/200694689/a%2Fb%20c"),
])
def test_url_without_a_usable_slug(slug, url):
    assert client._url({"transformedPostingTitle": slug}, "200694689") == url


@pytest.mark.parametrize("value", [None, "", "Nov 12, 2026", 1762387200, "2026-13-01T00:00:00Z"])
def test_posted_date_degrades_to_empty(value):
    assert client._posted_date({"postDateInGMT": value}) == ""


def test_department_without_a_team_is_blank():
    assert client._department({"team": None}) == ""
    assert client._department({}) == ""
