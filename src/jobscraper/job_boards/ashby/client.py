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
- Every posting already carries a ready-made `descriptionPlain` field, even
  on the plain (no extra params) list endpoint this client hits -- unlike
  Greenhouse's HTML `content` (see strip_html) or Lever's several
  plain-text pieces (see that client's `_description`), Job.description
  here is just that field verbatim. Same as every client, this only runs
  for a firm in models.FIRMS_NEEDING_DESCRIPTION; no live Ashby board is in
  that set today.
- Unlike every other field this client reads, compensation data is left out
  of the response entirely unless the request itself asks for it --
  confirmed: the plain list endpoint (no extra params) never includes a
  `compensation` key on any posting, on any board. `fetch_ashby_jobs`
  always requests `includeCompensation=true`, at which point a posting
  whose employer enabled pay transparency carries a `compensation` object;
  `_salary` reads its own `scrapeableCompensationSalarySummary` field (a
  short, plain string Ashby already formats for exactly this purpose, e.g.
  "$257K - $335K") rather than `compensationTierSummary` (the fuller
  string Ashby's own hosted page shows, which also appends "Offers Equity"/
  "Offers Commission" -- too much for a single sheet cell). Confirmed
  present on 13 of this project's 22 Ashby boards (e.g. OpenAI 673/818
  jobs, Exa 54/54, Sierra 183/209), absent -- an all-null
  `compensationTiers` -- on the rest (e.g. Notion, Cursor, Mistral). Unlike
  `description`, this is NOT gated to a firm allowlist -- see
  models.Job.salary's own docstring for why.

`firm` (not the slug) is what ends up in Job.board, for the same
readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="Ashby"` -- a fixed literal, not anything read from the response --
see models.Job's docstring.

Retries via job_boards._http.get_with_retries, the same shared retry policy
(transient failures, 3 attempts, exponential backoff) every board client
uses -- see that module's docstring for the reasoning.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target tests/ashby/test_client.py's
# monkeypatch.setattr(client.requests, "get", ...) patches, since `requests`
# is the same module object wherever it's imported, so patching it here
# still reaches _http's call.
import requests

from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


def fetch_ashby_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Ashby job board.

    `board_slug` is the Ashby job-board slug used to hit the API (e.g.
    "openai"); `firm` and `company_type` (both from
    company_toml/ashby_boards.toml) are stored as-is on every returned Job,
    same as the Greenhouse/Lever clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Ashby.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_slug}"
    response = get_with_retries(
        "ashby", board_slug, url, params={"includeCompensation": "true"}
    )

    payload = response.json()
    raw_jobs = payload.get("jobs", [])

    # Same defensive posture as the Greenhouse/Lever clients' fetch_*_jobs: one
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
        source="Ashby",
        description=(
            (raw.get("descriptionPlain") or "").strip()
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        salary=_salary(raw),
    )


def _department(raw: dict) -> str:
    """Org-unit label for this posting, or "" -- see the module docstring
    for why `team` is a real fallback here, not just a nice-to-have."""
    return raw.get("department") or raw.get("team") or ""


def _salary(raw: dict) -> str | None:
    """A human-readable pay range from `compensation`, or None if absent.

    See the module docstring for why `fetch_ashby_jobs` requests
    `includeCompensation=true` in the first place, and why
    `scrapeableCompensationSalarySummary` (not `compensationTierSummary`)
    is the field read here.
    """
    compensation = raw.get("compensation") or {}
    return (compensation.get("scrapeableCompensationSalarySummary") or "").strip() or None


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
