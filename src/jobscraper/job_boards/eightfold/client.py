"""Eightfold Apply API client."""

import datetime

from jobscraper.enrichment.normalize_location import normalize_locations
from jobscraper.job_boards._http import paginate_with_retries
from jobscraper.job_boards._jobs import build_job, jobs_from
from jobscraper.models import Job, normalize_date

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
    url = f"https://{board_slug}.eightfold.ai/api/apply/v2/jobs"
    raw_jobs = _fetch_all_pages(board_slug, url)
    return jobs_from(
        "eightfold", board_slug, raw_jobs, _to_job, firm, company_type, scraped_date
    )


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
    return build_job(
        "eightfold",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=raw.get("name", ""),
        department=raw.get("department", ""),
        location=_location(raw),
        url=raw.get("canonicalPositionUrl", ""),
        updated_at=_epoch_date(raw.get("t_update")),
        first_published=_epoch_date(raw.get("t_create")),
        description=lambda: (raw.get("job_description") or "").strip(),
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
