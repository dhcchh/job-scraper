"""Workday candidate-site API client.

One function: give it a board token, get back this run's Job objects -- same
shape as the Greenhouse/Lever/Ashby/SmartRecruiters/Eightfold clients,
adapted to Workday's response shape (confirmed against Visa's and
Mastercard's live boards):

- Every Workday careers site (`https://<tenant>.<wdN>.myworkdayjobs.com/
  <site>`) is backed by the same JSON API the site's own front-end calls,
  under `/wday/cxs/<tenant>/<site>`. It's undocumented, like Eightfold's --
  found from the site's own browser traffic, not a published API contract.
  All three pieces vary per company (Visa is `visa.wd5`/`Visa`, Mastercard
  is `mastercard.wd1`/`CorporateCareers`; the `wdN` data-center number isn't
  guessable from the name, e.g. `visa.wd1` 422s), so `token`
  (company_toml/workday_boards.toml) packs them into one
  `"<tenant>.<wdN>/<site>"` string -- see _parse_token.
- The job list is a POST (`/jobs`, a JSON search body), not a GET, even
  though it only reads. It's paginated via `offset`/`limit`, and `limit`
  above 20 is rejected outright with a 400, so pages are fixed at 20. The
  response's `total` is only filled in on the FIRST page -- every later page
  says `total: 0` -- which job_boards._http.paginate_with_retries tolerates
  by keeping the first page's total. At ~2s per page, Mastercard's ~1050
  postings take ~53 requests and ~100s, by far the slowest board per posting.
- A list item carries only `title`, `externalPath`, `locationsText`,
  `postedOn`, and `bulletFields`: no department, no edited-at timestamp, no
  compensation. So `department`/`updated_at` stay "" and `salary` stays None,
  same as Lever/Ashby/SmartRecruiters' no-edited-at shape.
- `postedOn` is relative text, not a date: "Posted Today", "Posted
  Yesterday", "Posted N Days Ago", or "Posted 30+ Days Ago" (~40% of both
  boards' postings). _posted_on_date turns the first three into a date
  counted back from `scraped_date` -- possibly a day off, since Workday
  counts days in its own timezone -- and leaves "30+ Days Ago" (or anything
  unrecognized) as "" rather than fabricate a date. Daily runs see a new
  posting within a day or two of it going live, so this is only a gap for
  postings already old when the board was first added.
- `locationsText` is one office's name ("Toronto, Canada", "US - Austin,
  TX") -- normalized whole, never comma-split, the same rule as every other
  board's free-text location -- EXCEPT for a posting open in more than one
  office, where it's just a count ("2 Locations"). The real list of offices
  is only on that posting's detail endpoint (`GET /wday/cxs/<tenant>/<site>
  <externalPath>`: `jobPostingInfo.location` + `additionalLocations[]`,
  which also carries an exact `startDate`). Each detail call takes ~1-4s,
  and ~160 of Visa's + Mastercard's ~1825 postings are multi-office, so
  _needs_detail only makes that call for a multi-office (or location-less)
  posting whose title classify_roles.classify_job actually routes to a
  category -- ~11 per day across both boards rather than ~160. A noise
  posting is dropped by main.py before its location is ever looked at, so
  its "N Locations" placeholder never reaches the sheet.
- `job_id` is the suffix of `externalPath` after its last "_" (e.g.
  "R-287924-1", "REF088713W" -- the requisition id, plus a "-N" when that
  req has been posted more than once), unique on both boards checked and
  unaffected by a title edit, unlike the title slug before it.
  `bulletFields[0]` is usually the same req id, but it's a tenant-configured
  field (missing on one live Visa posting), so it's not relied on.
- `url` is built as `https://<host>/<site><externalPath>`, which matches the
  detail endpoint's own `externalUrl` exactly.
- The detail endpoint's `jobDescription` has the full JD, but no Workday
  firm is in models.FIRMS_NEEDING_DESCRIPTION today, so `description` stays
  "" and no detail call is made for it; a future Workday firm needing it
  would have to add that call here.

`firm` (not the token) is what ends up in Job.board, for the same
readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="Workday"` -- a fixed literal, not anything read from the response
-- see models.Job's docstring.

Retries via job_boards._http, the same shared retry policy (transient
failures, 3 attempts, exponential backoff) every board client uses -- see
that module's docstring for the reasoning.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import re

# Not called directly here (job_boards._http does the actual GET/POST) --
# kept as the target tests/workday/test_client.py's monkeypatch.setattr(
# client.requests, "post"/"get", ...) patches, since `requests` is the same
# module object wherever it's imported, so patching it here still reaches
# _http's calls.
import requests

from jobscraper.enrichment.classify_roles import classify_job
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location
from jobscraper.job_boards._http import get_with_retries, paginate_with_retries
from jobscraper.models import Job, normalize_date

logger = logging.getLogger(__name__)

# The server's own maximum page size -- `limit` above this is a 400
# (confirmed against Mastercard's live board), not a silently capped page.
_PAGE_SIZE = 20

_TOKEN_RE = re.compile(r"^([A-Za-z0-9-]+)\.(wd\d+)/([^/\s]+)$")
_MULTI_LOCATION_RE = re.compile(r"^\d+ Locations?$", re.IGNORECASE)
_POSTED_DAYS_AGO_RE = re.compile(r"^posted (\d+) days? ago$", re.IGNORECASE)


def fetch_workday_jobs(
    token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Workday careers site.

    `token` is "<tenant>.<wdN>/<site>" (e.g. "visa.wd5/Visa" for
    visa.wd5.myworkdayjobs.com/Visa); `firm` and `company_type` (both from
    company_toml/workday_boards.toml) are stored as-is on every returned
    Job, same as the other five clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, and what a relative "Posted N
    Days Ago" is counted back from.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    host, api = _parse_token(token)
    raw_jobs = paginate_with_retries(
        "workday",
        token,
        f"{api}/jobs",
        method="POST",
        body={"appliedFacets": {}, "searchText": ""},
        cursor_param="offset",
        page_size_param="limit",
        page_size=_PAGE_SIZE,
        total_key="total",
        items_key="jobPostings",
    )
    site = api.rsplit("/", 1)[-1]

    # Same defensive posture as the other clients' fetch_*_jobs: one
    # malformed record (no externalPath, or one already seen on an earlier
    # page) is skipped rather than costing this whole board's otherwise-good
    # postings for the day, or than sheets.writer appending the same
    # (board, job_id) twice in one run.
    jobs = []
    skipped = 0
    detail_fetches = 0
    detail_failures = 0
    seen_ids: set[str] = set()
    for raw in raw_jobs:
        path = raw.get("externalPath")
        if not path:
            skipped += 1
            continue
        job_id = _job_id(path)
        if job_id in seen_ids:
            skipped += 1
            continue
        seen_ids.add(job_id)

        job = _to_job(raw, job_id, host, site, firm, company_type, scraped_date)
        if _needs_detail(raw, job):
            detail_fetches += 1
            try:
                detail = get_with_retries("workday", token, f"{api}{path}").json()
            except Exception:
                # Most likely the posting closed between the list call and
                # this one (a 404). Dropping just this posting for today is
                # self-healing either way: if it's really still open, the
                # next run picks it back up with its real offices, rather
                # than this run writing it into the wrong region's tab under
                # an "N Locations" placeholder.
                logger.warning(
                    "workday[%s]: couldn't fetch detail for %s, skipping it "
                    "this run",
                    token,
                    path,
                    exc_info=True,
                )
                detail_failures += 1
                continue
            job = _with_detail(job, detail.get("jobPostingInfo") or {})
        jobs.append(job)

    if skipped:
        logger.warning(
            "workday[%s]: skipped %d job(s) missing or duplicating an "
            "externalPath",
            token,
            skipped,
        )
    logger.info(
        "workday[%s]: fetched %d jobs (%d detail lookup(s), %d failed)",
        token,
        len(jobs),
        detail_fetches,
        detail_failures,
    )

    return jobs


def _parse_token(token: str) -> tuple[str, str]:
    """(host, api base URL) for a "<tenant>.<wdN>/<site>" token.

    The tenant appears twice in the API URL -- once in the host, once in
    the path -- which is why it isn't a separate config field.
    """
    match = _TOKEN_RE.match(token.strip())
    if not match:
        raise ValueError(
            f"workday token {token!r} isn't '<tenant>.<wdN>/<site>' "
            "(e.g. 'visa.wd5/Visa')"
        )
    tenant, data_center, site = match.groups()
    host = f"{tenant}.{data_center}.myworkdayjobs.com"
    return host, f"https://{host}/wday/cxs/{tenant}/{site}"


def _job_id(external_path: str) -> str:
    return external_path.rsplit("_", 1)[-1]


def _to_job(
    raw: dict,
    job_id: str,
    host: str,
    site: str,
    firm: str,
    company_type: str,
    scraped_date: datetime.date,
) -> Job:
    title = raw.get("title", "")
    locations_text = raw.get("locationsText") or ""
    return Job(
        board=firm,
        job_id=job_id,
        title=title,
        department="",
        location=(
            ""
            if _MULTI_LOCATION_RE.match(locations_text)
            else normalize_location(locations_text)
        ),
        url=f"https://{host}/{site}{raw['externalPath']}",
        updated_at="",
        first_published=_posted_on_date(raw.get("postedOn"), scraped_date),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, ""),
        company_type=company_type,
        source="Workday",
        description="",
        # No compensation field in the list or detail response -- confirmed
        # against Visa's and Mastercard's live boards -- see
        # models.Job.salary.
        salary=None,
    )


def _needs_detail(raw: dict, job: Job) -> bool:
    """Whether this posting's real offices are only on its detail endpoint,
    AND it's worth the extra request -- see the module docstring."""
    locations_text = raw.get("locationsText") or ""
    if locations_text and not _MULTI_LOCATION_RE.match(locations_text):
        return False
    return classify_job(job) is not None


def _with_detail(job: Job, info: dict) -> Job:
    """job with its location (every office, " | "-joined) and exact
    first_published taken from its detail response's `jobPostingInfo`.

    A posting with no posting location at all (one live Mastercard posting:
    `location: ""`, no `additionalLocations`) falls back to its
    requisition's own location, `jobRequisitionLocation.descriptor` --
    otherwise it'd go out with a blank location and land in RoW, whatever
    country it's really in (Pune, India, in that case, which is_excluded
    should drop).
    """
    entries = [info.get("location")] + list(info.get("additionalLocations") or [])
    names = _normalized_names(entries)
    if not names:
        names = _normalized_names(
            [(info.get("jobRequisitionLocation") or {}).get("descriptor")]
        )
    start_date = normalize_date(info.get("startDate"))
    return dataclasses.replace(
        job,
        location=" | ".join(names),
        first_published=start_date or job.first_published,
    )


def _normalized_names(entries: list[str | None]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        normalized = normalize_location(entry or "")
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def _posted_on_date(posted_on: str | None, scraped_date: datetime.date) -> str:
    """A "Posted ..." label as this project's YYYY-MM-DD date string, or ""
    when it doesn't say how many days ago ("Posted 30+ Days Ago", missing,
    or anything unrecognized)."""
    text = (posted_on or "").strip().lower()
    if text == "posted today":
        days_ago = 0
    elif text == "posted yesterday":
        days_ago = 1
    else:
        match = _POSTED_DAYS_AGO_RE.match(text)
        if not match:
            return ""
        days_ago = int(match.group(1))
    return normalize_date(scraped_date - datetime.timedelta(days=days_ago))
