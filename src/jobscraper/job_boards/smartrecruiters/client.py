"""SmartRecruiters Posting API client.

One function: give it a company identifier, get back this run's Job
objects -- same shape as job_boards/greenhouse/client.py,
job_boards/lever/client.py, and job_boards/ashby/client.py, adapted to
SmartRecruiters' response shape:

- The endpoint is paginated (`{"offset", "limit", "totalFound", "content":
  [...]}`), unlike the other three boards' single-response payloads --
  `limit` is capped at 100 by the server regardless of what's requested
  (confirmed against live Grab/Wise data, which each have 400+ postings), so
  fetch_smartrecruiters_jobs pages through with `offset` until every
  `totalFound` record is collected.
- A posting's title is `name`, not `title`; `id` is already a plain numeric
  string.
- There is exactly one `location` object per posting (`city`/`country`/
  `remote`/`hybrid`/`fullLocation`), not a pre-split per-office array like
  Greenhouse's `offices[]`/Lever's `categories.allLocations`/Ashby's
  `location` + `secondaryLocations[]` -- `_location` below normalizes just
  `location.city`, with no " | "-joining needed since a SmartRecruiters
  posting is never open in more than one office.
- The generic `department` object every posting carries is confirmed always
  `{}` on both Grab's and Wise's live boards (checked against every posting
  on each) -- whatever a company calls its own org unit lives in
  company-specific custom fields with no shared schema across companies
  (Grab has a "Department Name" custom field; Wise has none, its closest
  equivalent is "Tribe"/"Squad", Wise's own internal team jargon, not a
  general org-unit label). `function.label` is used instead --
  SmartRecruiters' own standardized function taxonomy ("Engineering",
  "Design", "Finance", ...), always populated and playing the same broad
  org-unit role as Greenhouse's `_first_name(departments)`.
- The list response's own `ref` field is the API endpoint URL for the
  posting, not a candidate-facing page. The real apply page follows the
  fixed pattern `https://jobs.smartrecruiters.com/{company
  identifier}/{id}` (confirmed against live Grab and Wise postings -- no
  redirect or slug suffix needed), built directly rather than read from a
  field or fetched from the separate per-posting detail endpoint.
- There is no edited-at timestamp in this API at all (same shape as
  Lever/Ashby) -- only `releasedDate`, a "went live" signal, so exactly like
  those two clients this stamps first_published from it and leaves
  updated_at "" rather than fabricating a value the source data doesn't
  have.
- Unlike Greenhouse/Lever/Ashby, the full JD text isn't in this endpoint's
  response at all -- it only exists on the separate per-posting detail
  endpoint (`GET .../postings/{id}`, under `jobAd.sections`). No live
  SmartRecruiters board is in models.FIRMS_NEEDING_DESCRIPTION today, so
  description is left "" unconditionally here rather than building an
  unused per-job detail-fetch path -- add it (paginated fetch already
  identifies every posting id) if a future SmartRecruiters firm needs it.

`firm` (not the company identifier) is what ends up in Job.board, for the
same readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="SmartRecruiters"` -- a fixed literal, not anything read from the
response -- see models.Job's docstring.

Retries via job_boards._http.get_with_retries, the same shared retry policy
(transient failures, 3 attempts, exponential backoff) every board client
uses -- see that module's docstring for the reasoning. One difference worth
knowing:
SmartRecruiters returns HTTP 200 with `totalFound: 0` for a nonexistent
company identifier rather than a 404 -- same "200 but empty" shape already
called out for Lever in this project's board-adding workflow, not something
this client can distinguish from a real company that (however unlikely)
currently has zero open postings.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target
# tests/smartrecruiters/test_client.py's monkeypatch.setattr(client.requests,
# "get", ...) patches, since `requests` is the same module object wherever
# it's imported, so patching it here still reaches _http's call.
import requests

from jobscraper.job_boards._http import paginate_with_retries
from jobscraper.models import Job, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
JOBS_SITE_URL = "https://jobs.smartrecruiters.com"

# The server's own cap -- requesting more (200, 500, ...) still returns 100
# (confirmed against live Grab/Wise data), so fetch_smartrecruiters_jobs
# pages in strides of this size rather than assuming a larger one is honored.
_PAGE_LIMIT = 100


def fetch_smartrecruiters_jobs(
    company_identifier: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's SmartRecruiters board.

    `company_identifier` is the SmartRecruiters company identifier used to
    hit the API (e.g. "Grab"); `firm` and `company_type` (both from
    company_toml/smartrecruiters_boards.toml) are stored as-is on every
    returned Job, same as the other clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from
    SmartRecruiters.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{company_identifier}/postings"
    raw_jobs = _fetch_all_pages(company_identifier, url)

    # Same defensive posture as the other clients' fetch_*_jobs: one
    # malformed record (no id at all) is skipped rather than costing this
    # whole board's otherwise-good postings for the day.
    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    if skipped:
        logger.warning(
            "smartrecruiters[%s]: skipped %d job(s) missing an id",
            company_identifier,
            skipped,
        )
    logger.info(
        "smartrecruiters[%s]: fetched %d jobs", company_identifier, len(jobs)
    )

    return jobs


def _fetch_all_pages(company_identifier: str, url: str) -> list[dict]:
    """Every posting across every page of the postings endpoint.

    Pages in strides of _PAGE_LIMIT (the server's own hard cap) using
    `offset`, stopping once `totalFound` postings have been collected or a
    page comes back empty (belt-and-braces against an off-by-one in
    `totalFound` looping forever) -- via job_boards._http.paginate_with_
    retries, shared with eightfold/client.py's own _fetch_all_pages.
    """
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
    title = raw.get("name", "")
    department = _department(raw)
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=_posting_url(raw),
        updated_at="",
        first_published=normalize_date(raw.get("releasedDate", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="SmartRecruiters",
        description="",
        # No compensation field anywhere in this API -- confirmed against
        # both the list response and a real per-posting detail response
        # (Grab) -- see models.Job.salary.
        salary="",
    )


def _department(raw: dict) -> str:
    """Org-unit label for this posting, or "" -- see the module docstring
    for why `function.label` is used instead of the always-empty
    `department` object or a per-company custom field."""
    return (raw.get("function") or {}).get("label", "")


def _location(raw: dict) -> str:
    """The one office this posting is open in, normalized -- see the module
    docstring for why SmartRecruiters never needs the " | "-joining every
    other client's `_location` does."""
    return normalize_location((raw.get("location") or {}).get("city") or "")


def _posting_url(raw: dict) -> str:
    """The candidate-facing apply page -- see the module docstring for why
    this is built rather than read from a field."""
    identifier = (raw.get("company") or {}).get("identifier", "")
    return f"{JOBS_SITE_URL}/{identifier}/{raw['id']}"
