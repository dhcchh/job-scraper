"""Apple careers-site (jobs.apple.com) client.

jobs.apple.com has no public API. Its own search page posts to
`/api/v1/search`, an undocumented, unauthenticated JSON endpoint; this
client uses the same endpoint. A board token lists the teams to fetch as
comma-separated team codes, each optionally narrowed to one sub-team:
"SFTWR,MLAI,STDNT/INTRN". One request covers them all, so a posting listed
under two teams comes back once.

The endpoint returns one row per office: a posting open in three offices is
three rows sharing `positionId`. Rows are grouped into one job per posting.
"""

import datetime
import functools
import logging
import urllib.parse

from jobscraper.enrichment.classify_region import names_a_country
from jobscraper.enrichment.normalize_location import normalize_locations, normalize_office
from jobscraper.job_boards.common.http import (
    IncompleteBoardResponse,
    get_with_retries,
    post_with_retries,
)
from jobscraper.job_boards.common.jobs import build_job, jobs_from
from jobscraper.job_boards.common.pagination import paginate_numbered_pages
from jobscraper.models import Job, normalize_job_id

logger = logging.getLogger(__name__)

_BASE_URL = "https://jobs.apple.com"
_SEARCH_URL = f"{_BASE_URL}/api/v1/search"
_TEAMS_URL = f"{_BASE_URL}/api/v1/refData/teamsofinterest"

# The endpoint ignores every page-size parameter tried (checked 2026-11-15).
_PAGE_SIZE = 20

# Four concurrent page streams ran without errors or HTTP 429 (2026-11-15).
_PAGE_WORKERS = 4

# Refetching the pages around a repeated row reached the full count within
# two rounds in every probe (2026-11-15).
_REPAIR_ROUNDS = 3

_COUNTRY_ID_PREFIX = "iso-country-"


def fetch_apple_jobs(
    token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize every open Apple posting in the token's teams."""
    filters = _team_filters(token)
    _check_teams(token, filters)
    rows = paginate_numbered_pages(
        "apple",
        token,
        functools.partial(_search_page, token, filters),
        page_size=_PAGE_SIZE,
        total_key="totalRecords",
        items_key="searchResults",
        workers=_PAGE_WORKERS,
        repair_rounds=_REPAIR_ROUNDS,
        check_first_page=functools.partial(_check_first_page, token=token),
    )
    return jobs_from(
        "apple", token, _postings(rows), _to_job, firm, company_type, scraped_date
    )


def _team_filters(token: str) -> list[dict]:
    """Turn "SFTWR,STDNT/INTRN" into the search body's team filters."""
    filters = []
    for entry in token.split(","):
        team, _, sub_team = entry.partition("/")
        team_filter = {"team": f"teamsAndSubTeams-{team}"}
        if sub_team:
            team_filter["subTeam"] = f"subTeam-{sub_team}"
        filters.append(team_filter)
    return filters


def _check_teams(token: str, filters: list[dict]) -> None:
    """Fail unless the site lists every team and sub-team in the token.

    An unknown code returns no rows rather than an error, so a renamed
    sub-team would silently drop its postings from a combined search, and
    every one of them would look closed.
    """
    payload = get_with_retries("apple", token, _TEAMS_URL).json()
    try:
        known = {
            team["id"]: {sub_team["id"] for sub_team in team["teams"]}
            for team in payload["res"]
        }
    except (KeyError, TypeError) as exc:
        raise IncompleteBoardResponse(f"apple[{token}]: unreadable team list") from exc
    unknown = [
        entry
        for entry, team_filter in zip(token.split(","), filters)
        if not _is_listed(team_filter, known)
    ]
    if unknown:
        raise IncompleteBoardResponse(
            f"apple[{token}]: the site lists no team {', '.join(unknown)}"
        )


def _is_listed(team_filter: dict, known: dict[str, set[str]]) -> bool:
    sub_teams = known.get(team_filter["team"])
    if sub_teams is None:
        return False
    return "subTeam" not in team_filter or team_filter["subTeam"] in sub_teams


def _search_page(token: str, filters: list[dict], page: int) -> dict:
    response = post_with_retries(
        "apple",
        token,
        _SEARCH_URL,
        json={
            "query": "",
            "filters": {"teams": filters},
            "page": page,
            "locale": "en-us",
            # Other sort orders skip rows across a full pass (2026-11-15).
            "sort": "newest",
            # Required: without it the response has no rows.
            "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
        },
    )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("res"), dict):
        raise IncompleteBoardResponse(f"apple[{token}]: missing 'res'")
    return payload["res"]


def _check_first_page(payload: dict, *, token: str) -> None:
    """Fail an empty search: a request the endpoint can't read returns no rows too."""
    if payload["totalRecords"] == 0:  # an int; the paginator validated it
        raise IncompleteBoardResponse(f"apple[{token}]: the search returned no rows")


def _postings(rows: list[dict]) -> list[dict]:
    """Group rows by posting as {"positionId", "rows"}, in a stable office order."""
    by_position: dict[str, list[dict]] = {}
    for row in rows:
        try:
            position_id = normalize_job_id(row.get("positionId"))
        except ValueError as exc:
            raise IncompleteBoardResponse(
                f"missing or invalid positionId: {exc}"
            ) from exc
        by_position.setdefault(position_id, []).append(row)
    return [
        {
            "positionId": position_id,
            "rows": sorted(position_rows, key=lambda row: str(row["id"])),
        }
        for position_id, position_rows in by_position.items()
    ]


def _to_job(
    posting: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    rows = posting["rows"]
    first = rows[0]
    job_id = posting["positionId"]
    return build_job(
        "apple",
        firm,
        company_type,
        scraped_date,
        job_id=job_id,
        title=" ".join(str(first.get("postingTitle") or "").split()),
        department=_department(first),
        location=_location(rows),
        url=_url(first, job_id),
        first_published=min(
            (date for date in map(_posted_date, rows) if date), default=""
        ),
        description=lambda: str(first.get("jobSummary") or "").strip(),
    )


def _department(row: dict) -> str:
    team = row.get("team")
    return str(team.get("teamName") or "") if isinstance(team, dict) else ""


def _url(row: dict, job_id: str) -> str:
    """Link the posting's own page; without the title slug it redirects."""
    url = f"{_BASE_URL}/en-us/details/{urllib.parse.quote(job_id, safe='')}"
    slug = row.get("transformedPostingTitle")
    if isinstance(slug, str) and slug.strip():
        return f"{url}/{urllib.parse.quote(slug.strip(), safe='')}"
    return url


def _posted_date(row: dict) -> str:
    """Take the date from `postDateInGMT` ("2026-11-13T23:59:15.107Z")."""
    value = row.get("postDateInGMT")
    if not isinstance(value, str):
        return ""
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def _location(rows: list[dict]) -> str:
    """Normalize and join the offices of every row of one posting.

    A malformed entry is skipped rather than failing the board: an office
    isn't part of a posting's identity.
    """
    offices = []
    for row in rows:
        entries = row.get("locations")
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            if isinstance(entry, dict):
                offices.append(_office(entry))
            else:
                logger.warning(
                    "apple job %s: skipping a malformed location entry",
                    row.get("positionId"),
                )
    return normalize_locations(offices)


def _office(entry: dict) -> str:
    """One office, preferring the shared ISO alpha-3 table over Apple's name.

    `countryID` is "iso-country-" followed by an alpha-3 code, and Apple
    names countries in its own way ("United States of America"). Codes
    missing from the shared table fall back to the country name.
    """
    name = str(entry.get("name") or "")
    country_id = str(entry.get("countryID") or "")
    code = country_id.removeprefix(_COUNTRY_ID_PREFIX) if country_id.startswith(
        _COUNTRY_ID_PREFIX
    ) else ""
    office = normalize_office(name, code)
    if names_a_country(office):
        return office
    return normalize_office(name, str(entry.get("countryName") or "")) or office
