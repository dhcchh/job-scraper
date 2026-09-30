"""Eightfold Apply API client."""

import datetime
import logging

from jobscraper.job_boards._http import paginate_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_job_id, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_locations

logger = logging.getLogger(__name__)

_PAGE_SIZE = 10

_WORKERS = 8


def fetch_eightfold_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for an Eightfold board."""
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"https://{board_slug}.eightfold.ai/api/apply/v2/jobs"
    raw_jobs = _fetch_all_pages(board_slug, url)

    jobs = []
    for raw in raw_jobs:
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    logger.info("eightfold[%s]: fetched %d jobs", board_slug, len(jobs))

    return jobs


def _fetch_all_pages(board_slug: str, url: str) -> list[dict]:
    """Fetch every jobs page."""
    return paginate_with_retries(
        "eightfold",
        board_slug,
        url,
        cursor_param="start",
        page_size_param="num",
        page_size=_PAGE_SIZE,
        total_key="count",
        items_key="positions",
        workers=_WORKERS,
    )


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = raw.get("name", "")
    department = raw.get("department", "")
    return Job(
        board=firm,
        job_id=normalize_job_id(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("canonicalPositionUrl", ""),
        updated_at=_epoch_date(raw.get("t_update")),
        first_published=_epoch_date(raw.get("t_create")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Eightfold",
        description=(
            (raw.get("job_description") or "").strip()
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        salary=None,
    )


def _location(raw: dict) -> str:
    """Normalize and join every listed office."""
    entries = raw.get("locations") or ([raw["location"]] if raw.get("location") else [])
    return normalize_locations(entries)


def _epoch_date(epoch_seconds: int | str | None) -> str:
    """Convert epoch seconds to an ISO date."""
    if not epoch_seconds:
        return ""
    try:
        epoch_seconds = int(epoch_seconds)
    except (TypeError, ValueError):
        return ""
    date = datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).date()
    return normalize_date(date)
