"""Recruitee careers API client."""

import datetime
import logging

from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import (
    normalize_location,
    normalize_locations,
)
from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_job_id, normalize_date, strip_html

logger = logging.getLogger(__name__)

URL_TEMPLATE = "https://{subdomain}.recruitee.com/api/offers/"


def fetch_recruitee_jobs(
    subdomain: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a Recruitee site."""
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = URL_TEMPLATE.format(subdomain=subdomain)
    response = get_with_retries("recruitee", subdomain, url)

    raw_jobs = response.json().get("offers", [])

    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    if skipped:
        logger.warning(
            "recruitee[%s]: skipped %d job(s) missing an id", subdomain, skipped
        )
    logger.info("recruitee[%s]: fetched %d jobs", subdomain, len(jobs))

    return jobs


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = (raw.get("title") or "").strip()
    department = (raw.get("department") or "").strip()
    return Job(
        board=firm,
        job_id=normalize_job_id(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("careers_url") or "",
        updated_at=normalize_date(raw.get("updated_at") or ""),
        first_published=normalize_date(raw.get("published_at") or ""),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Recruitee",
        description=_description(raw) if firm in FIRMS_NEEDING_DESCRIPTION else "",
        salary=None,
    )


def _description(raw: dict) -> str:
    """Combine the description and requirements as plain text."""
    parts = (strip_html(raw.get(field) or "") for field in ("description", "requirements"))
    return "\n\n".join(part for part in parts if part)


def _location(raw: dict) -> str:
    """Normalize and join every listed office."""
    location = normalize_locations(
        [entry.get("city") for entry in raw.get("locations") or []]
    )
    return location or normalize_location(raw.get("city") or "")
