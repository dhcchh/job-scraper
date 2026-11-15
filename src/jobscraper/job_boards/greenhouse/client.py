"""Greenhouse Job Board API client."""

import datetime
import functools

from jobscraper.enrichment.normalize_location import (
    normalize_location,
    normalize_locations,
)
from jobscraper.job_boards._http import get_with_retries, require_job_list
from jobscraper.job_boards._jobs import build_job, jobs_from
from jobscraper.models import Job, normalize_date, strip_html

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

_OFFICES_ARE_TEAMS: frozenset[str] = frozenset({"govtech", "lyft", "scaleai", "twitch"})


def fetch_greenhouse_jobs(
    board_token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a Greenhouse board."""
    url = f"{BASE_URL}/{board_token}/jobs"
    response = get_with_retries("greenhouse", board_token, url, params={"content": "true"})
    raw_jobs = require_job_list(response.json(), "greenhouse", board_token, key="jobs")
    to_job = functools.partial(
        _to_job, use_location_name=board_token in _OFFICES_ARE_TEAMS
    )
    return jobs_from(
        "greenhouse", board_token, raw_jobs, to_job, firm, company_type, scraped_date
    )


def _to_job(
    raw: dict,
    firm: str,
    company_type: str,
    scraped_date: datetime.date,
    *,
    use_location_name: bool = False,
) -> Job:
    return build_job(
        "greenhouse",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=raw.get("title", ""),
        department=_first_name(raw.get("departments")),
        location=_location(raw, use_location_name=use_location_name),
        url=raw.get("absolute_url", ""),
        updated_at=normalize_date(raw.get("updated_at", "")),
        first_published=normalize_date(raw.get("first_published", "")),
        employment_type=_employment_type(raw),
        description=lambda: strip_html(raw.get("content", "")),
    )


_EMPLOYMENT_TYPE_FIELD = "employment type"


def _employment_type(raw: dict) -> str:
    """Read the posting's Employment Type metadata value."""
    for item in raw.get("metadata") or []:
        if str(item.get("name") or "").strip().lower() == _EMPLOYMENT_TYPE_FIELD:
            value = item.get("value")
            return value if isinstance(value, str) else ""
    return ""


def _first_name(items: list[dict] | None) -> str:
    """Return the first name in a metadata list."""
    if not items:
        return ""
    return items[0].get("name", "")


def _location(raw: dict, *, use_location_name: bool = False) -> str:
    """Normalize and join every listed office."""
    if use_location_name:
        return normalize_location((raw.get("location") or {}).get("name", ""))
    location = normalize_locations(
        [office.get("name") for office in raw.get("offices") or []]
    )
    return location or normalize_location((raw.get("location") or {}).get("name", ""))
