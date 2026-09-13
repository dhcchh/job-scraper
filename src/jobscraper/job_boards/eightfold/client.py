"""Eightfold.ai job board client.

One function: give it a company's Eightfold subdomain (and its own company
domain, used as an API query param -- see below), get back this run's Job
objects -- same shape as the other four board clients, adapted to
Eightfold's response shape and to real, material differences from the four
other board platforms this project fetches from:

- Unlike Greenhouse/Lever/Ashby/SmartRecruiters, Eightfold has no published,
  documented public API contract. This endpoint
  (`https://<subdomain>.eightfold.ai/api/apply/v2/jobs`) was found by
  inspecting the network calls a real customer's rendered careers page
  (Millennium Management's, at mlp.eightfold.ai) makes to populate its own
  job list -- it's the same request a browser makes, not a committed
  contract, so Eightfold can change its shape or start requiring auth
  without notice in a way the other four boards' documented APIs don't.
- Every Eightfold customer gets its own branded subdomain
  (`<subdomain>.eightfold.ai`), same idea as Lever's/Ashby's per-company
  slug -- but this endpoint ALSO requires a `domain` query parameter naming
  the company's own domain (e.g. "mlp.com" for Millennium), confirmed
  required: a mismatched or unrecognized domain/subdomain pairing gets a
  403 `{"message": "Not authorized for ..."}` rather than the job list, so
  company_toml/eightfold_boards.toml carries both as separate required
  fields (`token` for the subdomain, `domain` for the query param) instead
  of the single identifier the other four board types need.
- This query pattern -- no auth, no session cookie, plain GET -- does NOT
  generalize to every Eightfold-hosted company: confirmed, not just
  suspected. Of 7 other real, verified Eightfold tenants tried (found via
  their own actual careers pages, not guessed subdomains) --
  Albemarle worked identically to Millennium's; Morgan Stanley, Micron,
  Eaton, and Vodafone all returned the same 403
  `{"message": "Not authorized for ..."}` even with the exact
  subdomain/domain pairing taken straight from each company's own live
  page -- so each Eightfold tenant clearly configures its own
  WAF/bot-protection independently, and only about 2 in 7 real Eightfold
  firms sampled this way were actually reachable this simply.
  Dry-run a real company's own careers site before adding it here, same as
  every other board type's own "Adding a board" workflow -- don't assume a
  new Eightfold firm works just because Millennium's does.
- The server ignores the requested page size entirely -- passing a larger
  `num` (confirmed up to 100) still returns exactly 10 postings per page,
  unlike SmartRecruiters' own (still real, just larger) 100-per-page
  server-enforced cap -- so fetch_eightfold_jobs pages through in strides
  of 10 via `start`, using the response's own `count` field the same way
  job_boards/smartrecruiters/client.py uses `totalFound`. This means
  roughly 10x as many requests per board as any other client here for a
  similarly-sized firm (Millennium's ~230 open postings take ~23 requests,
  not ~1-3).
- Unlike Lever/Ashby/SmartRecruiters (which expose no edited-at signal at
  all), Eightfold hands back BOTH `t_create` and `t_update` -- so, uniquely
  among the four non-Greenhouse boards, this client populates a real
  `updated_at` rather than leaving it "" for lack of source data. Both are
  Unix epoch seconds, not an ISO 8601 string like every other board here,
  so they need their own conversion (_epoch_to_date) before normalize_date.
- The list endpoint's `job_description` field was confirmed empty on every
  live Millennium posting -- same "no JD text in the list response" shape
  as SmartRecruiters, so description is left "" unconditionally here
  regardless of models.FIRMS_NEEDING_DESCRIPTION. Unlike SmartRecruiters,
  it's not confirmed whether a separate per-posting detail endpoint with
  real JD text even exists -- not investigated, since no live Eightfold
  firm needs FIRMS_NEEDING_DESCRIPTION treatment today; build that path
  only once one does.
- `id` (Eightfold's own internal numeric posting id, e.g. 755958456084) is
  used for Job.job_id, not `ats_job_id` (e.g. "REQ-30793", the underlying
  company ATS's own requisition number that Eightfold ingested this
  posting from) -- `id` is what canonicalPositionUrl is actually keyed on,
  same "job_id matches what the apply link uses" precedent as Greenhouse's
  and SmartRecruiters' numeric ids.
- `locations` is already a pre-split per-office array, same role as
  Greenhouse's `offices[]`/Lever's `categories.allLocations`/Ashby's
  `location` + `secondaryLocations[]` -- but each entry already comes as
  "City, Country" (e.g. "Singapore, Singapore", "Sydney, Australia") rather
  than a bare city name, so normalize_location.py's existing
  `_DUPLICATE_NAME_RE`/table lookups apply directly, with no new splitting
  logic needed. A country Eightfold spells differently than
  normalize_location.py's table expects (or a city not in the table at
  all) falls through unmapped -- same "needs its own dry-run tuning pass"
  caveat as every other board type when it was first added, not something
  fixed ahead of time here.

`firm` (not the subdomain/domain) is what ends up in Job.board, for the
same readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="Eightfold"` -- a fixed literal, not anything read from the
response -- see models.Job's docstring.

Retries the same way as the other four clients (transient failures, 3
attempts, exponential backoff) -- see job_boards/greenhouse/client.py's
docstring for the reasoning, unchanged here.
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

# Same reasoning and values as job_boards/greenhouse/client.py.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0

# The server's own hard cap, confirmed against live Millennium data --
# requesting more (confirmed up to 100) still returns exactly this many, per
# page -- see the module docstring.
_PAGE_LIMIT = 10


def fetch_eightfold_jobs(
    subdomain: str,
    domain: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Eightfold job board.

    `subdomain` is the company's own Eightfold subdomain (e.g. "mlp" for
    mlp.eightfold.ai); `domain` is that company's own domain, required as a
    query param by this endpoint (e.g. "mlp.com") -- see the module
    docstring for why both are needed, unlike the other four clients'
    single identifier. `firm` and `company_type` (both from
    company_toml/eightfold_boards.toml) are stored as-is on every returned
    Job, same as the other clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Eightfold.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"https://{subdomain}.eightfold.ai/api/apply/v2/jobs"
    raw_jobs = _fetch_all_pages(subdomain, domain, url)

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
            "eightfold[%s]: skipped %d job(s) missing an id", subdomain, skipped
        )
    logger.info("eightfold[%s]: fetched %d jobs", subdomain, len(jobs))

    return jobs


def _fetch_all_pages(subdomain: str, domain: str, url: str) -> list[dict]:
    """Every posting across every page of the jobs endpoint.

    Pages in strides of _PAGE_LIMIT (the server's own hard cap, ignoring
    whatever `num` is requested) using `start`, stopping once the
    response's own `count` postings have been collected or a page comes
    back empty (belt-and-braces against an off-by-one in `count` looping
    forever) -- same shape as
    job_boards/smartrecruiters/client.py's `_fetch_all_pages`.
    """
    postings: list[dict] = []
    start = 0
    total_count = None
    while total_count is None or start < total_count:
        response = _get_with_retries(
            subdomain,
            url,
            params={"domain": domain, "start": start, "num": _PAGE_LIMIT},
        )
        payload = response.json()
        total_count = payload.get("count", 0)
        page = payload.get("positions", [])
        if not page:
            break
        postings.extend(page)
        start += len(page)
    return postings


def _get_with_retries(subdomain: str, url: str, *, params: dict) -> requests.Response:
    """GET url, retrying transient failures with exponential backoff.

    Identical policy to job_boards/greenhouse/client.py's helper of the same
    name -- see that module's docstring for the reasoning.
    """
    attempt = 1
    while True:
        try:
            response = requests.get(url, params=params, timeout=30)
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
                "eightfold[%s]: attempt %d/%d failed (%s), retrying in %.0fs",
                subdomain,
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
    title = raw.get("name", "")
    department = _department(raw)
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("canonicalPositionUrl", ""),
        updated_at=_epoch_to_date(raw.get("t_update")),
        first_published=_epoch_to_date(raw.get("t_create")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Eightfold",
        description="",
    )


def _epoch_to_date(epoch_seconds: int | None) -> str:
    """A Unix-epoch-seconds timestamp as normalize_date's ISO date string,
    or "" for a missing/falsy value -- see the module docstring for why
    Eightfold's timestamps need this conversion every other board's client
    doesn't (theirs are already ISO 8601 strings)."""
    if not epoch_seconds:
        return ""
    return normalize_date(
        datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc)
    )


def _department(raw: dict) -> str:
    """Org-unit label for this posting, or "" -- `business_unit` is a
    fallback for a record that ever omits `department`, unconfirmed
    necessary on live data so far (every Millennium posting checked has
    had `department` set) but consistent with how job_boards/lever/client.py
    and job_boards/ashby/client.py fall back to `team`."""
    return raw.get("department") or raw.get("business_unit") or ""


def _location(raw: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    `locations` is Eightfold's own pre-split per-office list -- see the
    module docstring for why each entry already comes as "City, Country"
    rather than a bare city name. Falls back to the singular `location`
    field (wrapped in a one-element list) for the rare record missing
    `locations` entirely, rather than dropping the office silently.
    """
    raw_locations = raw.get("locations") or (
        [raw["location"]] if raw.get("location") else []
    )
    names: list[str] = []
    for entry in raw_locations:
        normalized = normalize_location(entry or "")
        if normalized and normalized not in names:
            names.append(normalized)
    return " | ".join(names)
