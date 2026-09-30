"""Ashby Posting API client."""

import datetime
import logging

from jobscraper.job_boards._http import get_with_retries, require_job_list
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_job_id, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_locations

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


def fetch_ashby_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for an Ashby board."""
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_slug}"
    response = get_with_retries(
        "ashby", board_slug, url, params={"includeCompensation": "true"}
    )

    payload = response.json()
    raw_jobs = require_job_list(payload, "ashby", board_slug, key="jobs")

    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    if skipped:
        logger.warning(
            "ashby[%s]: skipped %d job(s) missing an id", board_slug, skipped
        )
    logger.info("ashby[%s]: fetched %d jobs", board_slug, len(jobs))

    return jobs


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = raw.get("title", "")
    department = _department(raw)
    return Job(
        board=firm,
        job_id=normalize_job_id(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("jobUrl", ""),
        updated_at="",
        first_published=normalize_date(raw.get("publishedAt", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Ashby",
        description=(
            (raw.get("descriptionPlain") or "").strip()
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        salary=_salary(raw),
        jd_raw=raw.get("descriptionHtml") or raw.get("descriptionPlain") or "",
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
