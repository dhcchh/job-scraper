"""Build `Job` objects the same way in every provider client."""

import datetime
import logging
from collections.abc import Callable, Iterable

from jobscraper.board_config import PROVIDER_LABELS
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.models import (
    FIRMS_NEEDING_DESCRIPTION,
    Job,
    normalize_job_id,
    strip_html,
)

logger = logging.getLogger(__name__)

# A client's converter: (raw posting, firm, company_type, scraped_date).
ToJob = Callable[[dict, str, str, datetime.date], Job]


def build_job(
    provider: str,
    firm: str,
    company_type: str,
    scraped_date: datetime.date,
    *,
    job_id: object,
    title: str,
    location: str,
    url: str,
    department: str = "",
    updated_at: str = "",
    first_published: str = "",
    employment_type: str = "",
    salary: str | None = None,
    description: Callable[[], str] | None = None,
) -> Job:
    """Build one posting, deriving the fields no provider reads differently.

    The source label, the seniority and the posting identity come from here.
    `description` is called only for a firm in `FIRMS_NEEDING_DESCRIPTION`,
    so no client extracts one for any other firm.
    """
    return Job(
        board=firm,
        job_id=normalize_job_id(job_id),
        title=title,
        department=department,
        location=location,
        url=url,
        updated_at=updated_at,
        first_published=first_published,
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department, employment_type),
        company_type=company_type,
        source=PROVIDER_LABELS[provider],
        description=(
            description()
            if description is not None and firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        salary=salary,
    )


def jobs_from(
    provider: str,
    token: str,
    raw_jobs: Iterable[dict],
    to_job: ToJob,
    firm: str,
    company_type: str,
    scraped_date: datetime.date | None,
) -> list[Job]:
    """Convert one board's raw postings, dated today unless told otherwise."""
    if scraped_date is None:
        scraped_date = datetime.date.today()
    jobs = [to_job(raw, firm, company_type, scraped_date) for raw in raw_jobs]
    logger.info("%s[%s]: fetched %d jobs", provider, token, len(jobs))
    return jobs


def html_sections(raw: dict, fields: Iterable[str]) -> str:
    """Join the named HTML fields as plain-text paragraphs, skipping empty ones."""
    parts = (strip_html(raw.get(field) or "") for field in fields)
    return "\n\n".join(part for part in parts if part)
