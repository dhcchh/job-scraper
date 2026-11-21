"""Ashby Posting API client."""

import datetime

from jobscraper.enrichment.normalize_location import normalize_locations
from jobscraper.job_boards.common.http import get_with_retries, require_job_list
from jobscraper.job_boards.common.jobs import build_job, jobs_from
from jobscraper.models import Job, normalize_date

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


def fetch_ashby_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for an Ashby board."""
    url = f"{BASE_URL}/{board_slug}"
    response = get_with_retries(
        "ashby", board_slug, url, params={"includeCompensation": "true"}
    )
    raw_jobs = require_job_list(response.json(), "ashby", board_slug, key="jobs")
    return jobs_from(
        "ashby", board_slug, raw_jobs, _to_job, firm, company_type, scraped_date
    )


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    return build_job(
        "ashby",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=raw.get("title", ""),
        department=_department(raw),
        location=_location(raw),
        url=raw.get("jobUrl", ""),
        first_published=normalize_date(raw.get("publishedAt", "")),
        description=lambda: (raw.get("descriptionPlain") or "").strip(),
        salary=_salary(raw),
    )


def _department(raw: dict) -> str:
    """Return the department, falling back to team."""
    return raw.get("department") or raw.get("team") or ""


def _salary(raw: dict) -> str | None:
    """Format Ashby's compensation summary when present."""
    compensation = raw.get("compensation") or {}
    return (compensation.get("scrapeableCompensationSalarySummary") or "").strip() or None


def _location(raw: dict) -> str:
    """Normalize and join every listed office."""
    locations = [raw.get("location")]
    locations.extend(
        secondary.get("location") for secondary in raw.get("secondaryLocations") or []
    )
    return normalize_locations(locations)
