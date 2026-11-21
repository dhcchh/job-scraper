"""Lever Postings API client."""

import datetime

from jobscraper.enrichment.normalize_location import (
    normalize_location,
    normalize_locations,
)
from jobscraper.job_boards.common.http import get_with_retries, require_job_list
from jobscraper.job_boards.common.jobs import build_job, jobs_from
from jobscraper.models import Job, normalize_date, strip_html

BASE_URL = "https://api.lever.co/v0/postings"


def fetch_lever_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a Lever site."""
    url = f"{BASE_URL}/{board_slug}"
    response = get_with_retries("lever", board_slug, url, params={"mode": "json"})
    raw_jobs = require_job_list(response.json(), "lever", board_slug)
    return jobs_from(
        "lever", board_slug, raw_jobs, _to_job, firm, company_type, scraped_date
    )


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    categories = raw.get("categories") or {}
    return build_job(
        "lever",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=raw.get("text", ""),
        department=_department(categories),
        location=_location(categories),
        url=raw.get("hostedUrl", ""),
        first_published=normalize_date(_created_at(raw)),
        description=lambda: _description(raw),
        salary=_salary(raw),
    )


def _department(categories: dict) -> str:
    """Return the department, falling back to team."""
    return categories.get("department") or categories.get("team") or ""


def _location(categories: dict) -> str:
    """Normalize and join every listed office."""
    location = normalize_locations(categories.get("allLocations") or [])
    return location or normalize_location(categories.get("location") or "")


def _description(raw: dict) -> str:
    """Assemble Lever's plain-text description fields."""
    sections = [
        (raw.get("openingPlain") or "").strip(),
        (raw.get("descriptionBodyPlain") or "").strip(),
    ]
    for item in raw.get("lists") or []:
        header = (item.get("text") or "").strip()
        body = strip_html(item.get("content") or "")
        sections.append("\n".join(part for part in (header, body) if part))
    sections.append((raw.get("additionalPlain") or "").strip())
    return "\n\n".join(section for section in sections if section)


def _salary(raw: dict) -> str | None:
    """Format Lever's structured salary range when present."""
    salary_range = raw.get("salaryRange")
    if not salary_range:
        return None
    minimum = salary_range.get("min")
    maximum = salary_range.get("max")
    if minimum is None or maximum is None:
        return None
    amount = f"{minimum:,}" if minimum == maximum else f"{minimum:,}-{maximum:,}"
    text = " ".join(part for part in (salary_range.get("currency", ""), amount) if part)
    interval = _interval_label(salary_range.get("interval", ""))
    return f"{text}/{interval}" if interval else text


def _interval_label(interval: str) -> str:
    if interval.startswith("per-") and interval.endswith("-salary"):
        return interval[len("per-") : -len("-salary")]
    return interval


def _created_at(raw: dict) -> datetime.date | None:
    """Convert Lever's epoch-millisecond creation time to a date."""
    epoch_ms = raw.get("createdAt")
    if not epoch_ms:
        return None
    return datetime.datetime.fromtimestamp(
        epoch_ms / 1000, tz=datetime.timezone.utc
    ).date()
