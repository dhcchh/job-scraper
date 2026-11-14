"""Greenhouse Job Board API client."""

import datetime
import logging

from jobscraper.board_config import PROVIDER_LABELS
from jobscraper.job_boards._http import get_with_retries, require_job_list
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_job_id, normalize_date, strip_html
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import (
    normalize_location,
    normalize_locations,
)

logger = logging.getLogger(__name__)

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
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_token}/jobs"
    response = get_with_retries("greenhouse", board_token, url, params={"content": "true"})

    payload = response.json()
    raw_jobs = require_job_list(payload, "greenhouse", board_token, key="jobs")

    jobs = []
    for raw in raw_jobs:
        jobs.append(
            _to_job(
                raw,
                firm,
                company_type,
                scraped_date,
                use_location_name=board_token in _OFFICES_ARE_TEAMS,
            )
        )
    logger.info("greenhouse[%s]: fetched %d jobs", board_token, len(jobs))

    return jobs


def _to_job(
    raw: dict,
    firm: str,
    company_type: str,
    scraped_date: datetime.date,
    *,
    use_location_name: bool = False,
) -> Job:
    title = raw.get("title", "")
    department = _first_name(raw.get("departments"))
    return Job(
        board=firm,
        job_id=normalize_job_id(raw["id"]),
        title=title,
        department=department,
        location=_location(raw, use_location_name=use_location_name),
        url=raw.get("absolute_url", ""),
        updated_at=normalize_date(raw.get("updated_at", "")),
        first_published=normalize_date(raw.get("first_published", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department, _employment_type(raw)),
        company_type=company_type,
        source=PROVIDER_LABELS["greenhouse"],
        description=(
            strip_html(raw.get("content", ""))
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        salary=None,
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
