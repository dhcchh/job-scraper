"""Eightfold Talent Platform Apply API client.

One function: give it a board slug, get back this run's Job objects -- same
shape as the Greenhouse/Lever/Ashby/SmartRecruiters clients, adapted to
Eightfold's response shape (confirmed against Millennium's live board,
mlp.eightfold.ai):

- Unlike every other client here, there's no single shared host -- each
  company gets its own Eightfold subdomain (`<slug>.eightfold.ai`), so
  `board_slug` is used to build the base URL itself, not just the request
  path. A `domain` query param (the company's own corporate domain, e.g.
  "mlp.com") shows up in the site's own browser traffic, but confirmed
  identical responses with and without it -- the subdomain alone already
  identifies the company -- so it's left off rather than carrying a second,
  redundant identifier per board in company_toml.
- The endpoint is paginated via `start`/`num`, but `num` is not honored --
  the server always returns exactly 10 positions per page regardless of what
  `num` asks for (confirmed requesting 10/50/100/200/500, all returning 10),
  unlike SmartRecruiters' client-adjustable-but-server-capped `limit`. So
  `_fetch_all_pages` pages in fixed strides of `_PAGE_SIZE`, using the
  response's own `count` as the stopping point.
- A posting's title is `name`; `department` is a plain top-level string
  (Millennium's live board always sets it -- "Information Technology",
  "Trading", "Legal", ...) unlike SmartRecruiters' always-empty `department`
  object.
- `locations` is Eightfold's own pre-split per-office list, the same role
  Greenhouse's `offices[]`/Lever's `categories.allLocations`/Ashby's
  `location` + `secondaryLocations[].location` play -- but each entry is
  itself a hierarchical "City, Region, Country" or "City, Country" string
  (e.g. "Bangalore, Karnataka, India", "Singapore, Singapore") rather than a
  bare city name. `_location` below keeps just the leading city segment of
  each entry before handing it to normalize_location.py -- that module's
  lookup table is keyed by bare city names (it's what every other client
  already feeds it), and re-deriving the country from the city there (rather
  than trusting Eightfold's own trailing segment) is what collapses
  Eightfold's redundant "Tokyo, Tokyo, Japan"/"Shanghai, Shanghai, China"
  duplication into the same clean "Tokyo, Japan"/"Shanghai, China" every
  other client already produces, and normalizes "Dubai, United Arab
  Emirates" to this project's existing "Dubai, UAE" convention with no
  changes to normalize_location.py itself.
- Unlike Lever/Ashby/SmartRecruiters (no edited-at signal at all) but like
  Greenhouse, Eightfold gives a genuine last-edited timestamp separate from
  first-published: `t_create`/`t_update`, both Unix epoch seconds (confirmed
  differing on 9 of Millennium's 230 live postings, so this is real signal,
  not always-equal noise) -- `_epoch_date` converts each to the same
  YYYY-MM-DD form normalize_date() already produces from every other
  client's own date fields.
- `canonicalPositionUrl` is the candidate-facing apply page, already a full
  URL (unlike SmartRecruiters, nothing needs constructing here).
- The list endpoint's own `job_description` field is always "" on every
  live posting checked -- getting real JD text would mean an extra
  per-posting detail fetch this client doesn't do, since no live Eightfold
  board is in models.FIRMS_NEEDING_DESCRIPTION today; add that fetch here
  (same "only for a firm in that set" gating every other client uses) if a
  future Eightfold firm needs it.

`firm` (not the slug) is what ends up in Job.board, for the same
readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="Eightfold"` -- a fixed literal, not anything read from the
response -- see models.Job's docstring.

Retries via job_boards._http.get_with_retries, the same shared retry policy
(transient failures, 3 attempts, exponential backoff) every board client
uses -- see that module's docstring for the reasoning.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target
# tests/eightfold/test_client.py's monkeypatch.setattr(client.requests,
# "get", ...) patches, since `requests` is the same module object wherever
# it's imported, so patching it here still reaches _http's call.
import requests

from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

# The server's own fixed page size -- requesting more (50, 100, ...) still
# returns 10 (confirmed against Millennium's live board), so
# _fetch_all_pages pages in strides of this size rather than assuming a
# larger one is honored.
_PAGE_SIZE = 10


def fetch_eightfold_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Eightfold board.

    `board_slug` is the company's Eightfold subdomain (e.g. "mlp" for
    mlp.eightfold.ai); `firm` and `company_type` (both from
    company_toml/eightfold_boards.toml) are stored as-is on every returned
    Job, same as the other four clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Eightfold.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"https://{board_slug}.eightfold.ai/api/apply/v2/jobs"
    raw_jobs = _fetch_all_pages(board_slug, url)

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
            "eightfold[%s]: skipped %d job(s) missing an id", board_slug, skipped
        )
    logger.info("eightfold[%s]: fetched %d jobs", board_slug, len(jobs))

    return jobs


def _fetch_all_pages(board_slug: str, url: str) -> list[dict]:
    """Every posting across every page of the jobs endpoint.

    Pages in fixed strides of _PAGE_SIZE (the server's own hard cap) using
    `start`, stopping once the response's own `count` has been collected or
    a page comes back empty (belt-and-braces against an off-by-one in
    `count` looping forever) -- same shape as
    smartrecruiters.client._fetch_all_pages.
    """
    positions: list[dict] = []
    start = 0
    total: int | None = None
    while total is None or start < total:
        response = get_with_retries(
            "eightfold", board_slug, url, params={"start": start, "num": _PAGE_SIZE}
        )
        payload = response.json()
        total = payload.get("count", 0)
        page = payload.get("positions", [])
        if not page:
            break
        positions.extend(page)
        start += len(page)
    return positions


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = raw.get("name", "")
    department = raw.get("department", "")
    return Job(
        board=firm,
        job_id=str(raw["id"]),
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
    )


def _location(raw: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    See the module docstring for why only the leading city segment of each
    "City, Region, Country"-shaped `locations[]` entry is kept before
    normalizing. Falls back to the singular `location` field only if
    `locations` is ever missing/empty, same defensive shape as
    job_boards/greenhouse/client.py's offices/location fallback.
    """
    entries = raw.get("locations") or ([raw["location"]] if raw.get("location") else [])
    names: list[str] = []
    for entry in entries:
        city = (entry or "").split(",", 1)[0].strip()
        normalized = normalize_location(city)
        if normalized and normalized not in names:
            names.append(normalized)
    return " | ".join(names)


def _epoch_date(epoch_seconds: int | None) -> str:
    """A Unix epoch (seconds) as this project's YYYY-MM-DD date string, or ""
    for a missing/zero value."""
    if not epoch_seconds:
        return ""
    date = datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).date()
    return normalize_date(date)
