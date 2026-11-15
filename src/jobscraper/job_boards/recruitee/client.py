"""Recruitee careers API client."""

import datetime

from jobscraper.enrichment.normalize_location import (
    normalize_office,
    normalize_locations,
)
from jobscraper.job_boards._http import get_with_retries, require_job_list
from jobscraper.job_boards._jobs import build_job, html_sections, jobs_from
from jobscraper.models import Job, normalize_date

URL_TEMPLATE = "https://{subdomain}.recruitee.com/api/offers/"


def fetch_recruitee_jobs(
    subdomain: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a Recruitee site."""
    url = URL_TEMPLATE.format(subdomain=subdomain)
    response = get_with_retries("recruitee", subdomain, url)
    raw_jobs = require_job_list(response.json(), "recruitee", subdomain, key="offers")
    return jobs_from(
        "recruitee", subdomain, raw_jobs, _to_job, firm, company_type, scraped_date
    )


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    return build_job(
        "recruitee",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=(raw.get("title") or "").strip(),
        department=(raw.get("department") or "").strip(),
        location=_location(raw),
        url=raw.get("careers_url") or "",
        updated_at=normalize_date(raw.get("updated_at") or ""),
        first_published=normalize_date(raw.get("published_at") or ""),
        description=lambda: html_sections(raw, ("description", "requirements")),
    )


def _location(raw: dict) -> str:
    """Normalize and join every listed office."""
    location = normalize_locations(
        [normalize_office(entry.get("city") or "", entry.get("country") or "")
         for entry in raw.get("locations") or []]
    )
    return location or normalize_office(raw.get("city") or "", raw.get("country") or "")
