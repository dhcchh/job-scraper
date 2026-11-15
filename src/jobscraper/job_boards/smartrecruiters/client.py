"""SmartRecruiters Posting API client."""

import datetime

from jobscraper.enrichment.normalize_location import normalize_office
from jobscraper.job_boards._http import paginate_with_retries
from jobscraper.job_boards._jobs import build_job, jobs_from
from jobscraper.models import Job, normalize_date

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
JOBS_SITE_URL = "https://jobs.smartrecruiters.com"

_PAGE_LIMIT = 100


def fetch_smartrecruiters_jobs(
    company_identifier: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a SmartRecruiters company."""
    url = f"{BASE_URL}/{company_identifier}/postings"
    raw_jobs = _fetch_all_pages(company_identifier, url)
    return jobs_from(
        "smartrecruiters",
        company_identifier,
        raw_jobs,
        _to_job,
        firm,
        company_type,
        scraped_date,
    )


def _fetch_all_pages(company_identifier: str, url: str) -> list[dict]:
    """Fetch every postings page."""
    return paginate_with_retries(
        "smartrecruiters",
        company_identifier,
        url,
        cursor_param="offset",
        page_size_param="limit",
        page_size=_PAGE_LIMIT,
        total_key="totalFound",
        items_key="content",
    )


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    return build_job(
        "smartrecruiters",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=raw.get("name", ""),
        department=_department(raw),
        location=_location(raw),
        url=_posting_url(raw),
        first_published=normalize_date(raw.get("releasedDate", "")),
    )


def _department(raw: dict) -> str:
    """Return the posting's functional org-unit label."""
    return (raw.get("function") or {}).get("label", "")


def _location(raw: dict) -> str:
    """Normalize the posting's single office."""
    location = raw.get("location") or {}
    return normalize_office(location.get("city") or "", location.get("country") or "")


def _posting_url(raw: dict) -> str:
    """Build the candidate-facing apply URL."""
    identifier = (raw.get("company") or {}).get("identifier", "")
    return f"{JOBS_SITE_URL}/{identifier}/{raw['id']}"
