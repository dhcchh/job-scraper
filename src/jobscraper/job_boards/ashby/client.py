"""Ashby Job Board Posting API client.

One function: give it a board slug, get back this run's Job objects -- same
shape as job_boards/greenhouse/client.py and job_boards/lever/client.py,
adapted to Ashby's response shape:

- The endpoint returns {"jobs": [...]}, like Greenhouse (not a bare array,
  like Lever).
- `department` is set directly on every posting confirmed live on this
  project's boards, but `_department` still falls back to `team` the same
  way job_boards/lever/client.py's `_department` does, for a record that
  ever omits it.
- Per-office locations are Ashby's own pre-split list, same role Greenhouse's
  `offices[]`/Lever's `categories.allLocations` play (see those clients'
  `_location`) -- but split across two fields here: the primary office is
  the top-level `location` string, and every other office is
  `secondaryLocations[].location`. `_location` below builds the same
  " | "-joined, normalized, deduplicated list from both.
- There is no edited-at timestamp in this API at all (confirmed against the
  live schema across every board this project fetches) -- only
  `publishedAt`, a "went live" signal, so exactly like the Lever client this
  stamps first_published from it and leaves updated_at "" rather than
  fabricating a value the source data doesn't have.
- `jobUrl` (the listing page a candidate lands on and applies from) is used
  for Job.url, not `applyUrl` (a deep link straight to the application
  form) -- matching absolute_url/hostedUrl's role in the other two clients.

`firm` (not the slug) is what ends up in Job.board, for the same
readability and dedup-key reasons documented in
job_boards/greenhouse/client.py.

Retries the same way as the Greenhouse/Lever clients (transient failures, 3
attempts, exponential backoff) -- see that module's docstring for the
reasoning, unchanged here.
"""

from __future__ import annotations

import datetime
import logging
import time

import requests

from jobscraper.models import Job, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

# Same reasoning and values as job_boards/greenhouse/client.py.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


def fetch_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Ashby job board.

    `board_slug` is the Ashby job-board slug used to hit the API (e.g.
    "openai"); `firm` and `company_type` (both from ashby_boards.toml) are
    stored as-is on every returned Job, same as the Greenhouse/Lever clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Ashby.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_slug}"
    response = _get_with_retries(board_slug, url)

    payload = response.json()
    raw_jobs = payload.get("jobs", [])

    # Same defensive posture as the Greenhouse/Lever clients' fetch_jobs: one
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
            "ashby[%s]: skipped %d job(s) missing an id", board_slug, skipped
        )
    logger.info("ashby[%s]: fetched %d jobs", board_slug, len(jobs))

    return jobs


def _get_with_retries(board_slug: str, url: str) -> requests.Response:
    """GET url, retrying transient failures with exponential backoff.

    Identical policy to job_boards/greenhouse/client.py's helper of the same
    name -- see that module's docstring for the reasoning.
    """
    attempt = 1
    while True:
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ) as exc:
            if attempt >= _MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            delay = _BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "ashby[%s]: attempt %d/%d failed (%s), retrying in %.0fs",
                board_slug,
                attempt,
                _MAX_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
            attempt += 1


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = raw.get("title", "")
    department = _department(raw)
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("jobUrl", ""),
        updated_at="",
        first_published=normalize_date(raw.get("publishedAt", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
    )


def _department(raw: dict) -> str:
    """Org-unit label for this posting, or "" -- see the module docstring
    for why `team` is a real fallback here, not just a nice-to-have."""
    return raw.get("department") or raw.get("team") or ""


def _location(raw: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    Ashby splits its own pre-split per-office list across two fields: the
    primary office is the top-level `location` string, every other office is
    `secondaryLocations[].location` -- both are already one-office-per-entry,
    the same role Greenhouse's `offices[]`/Lever's `categories.allLocations`
    play (see those clients' `_location`), so neither needs (or gets) the
    comma-splitting normalize_location.py's own docstring warns against.
    """
    names: list[str] = []
    primary = normalize_location(raw.get("location") or "")
    if primary:
        names.append(primary)
    for secondary in raw.get("secondaryLocations") or []:
        normalized = normalize_location(secondary.get("location") or "")
        if normalized and normalized not in names:
            names.append(normalized)
    return " | ".join(names)
