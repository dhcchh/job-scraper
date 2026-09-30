"""Workday candidate-site API client."""

import dataclasses
import datetime
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from requests.exceptions import HTTPError

from jobscraper.enrichment.classify_roles import classify_job
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import (
    normalize_location,
    normalize_locations,
)
from jobscraper.job_boards._http import (
    IncompleteBoardResponse,
    get_with_retries,
    paginate_with_retries,
)
from jobscraper.models import Job, normalize_date

logger = logging.getLogger(__name__)

_PAGE_SIZE = 20

_WORKERS = 8

_TOKEN_RE = re.compile(r"^([A-Za-z0-9-]+)\.(wd\d+)/([^/\s]+)$")
_MULTI_LOCATION_RE = re.compile(r"^\d+ Locations?$", re.IGNORECASE)
_POSTED_DAYS_AGO_RE = re.compile(r"^posted (\d+) days? ago$", re.IGNORECASE)


def fetch_workday_jobs(
    token: str,
    firm: str,
    company_type: str,
    *,
    location_scope: str | None = None,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize all open jobs for a Workday site."""
    if scraped_date is None:
        scraped_date = datetime.date.today()

    host, api = _parse_token(token)
    raw_jobs = paginate_with_retries(
        "workday",
        token,
        f"{api}/jobs",
        method="POST",
        body={"appliedFacets": {}, "searchText": location_scope or ""},
        cursor_param="offset",
        page_size_param="limit",
        page_size=_PAGE_SIZE,
        total_key="total",
        items_key="jobPostings",
        id_key="externalPath",
        workers=_WORKERS,
    )
    site = api.rsplit("/", 1)[-1]
    workday_today = _workday_today(token, api, raw_jobs) or scraped_date

    jobs: list[Job] = []
    needs_detail: list[tuple[int, str]] = []  # (index into jobs, externalPath)
    seen_ids: set[str] = set()
    for raw in raw_jobs:
        path = raw.get("externalPath")
        job_id = _job_id(path)
        if job_id in seen_ids:
            raise IncompleteBoardResponse(f"workday[{token}]: duplicate job_id {job_id}")
        seen_ids.add(job_id)

        job = _to_job(
            raw, job_id, host, site, firm, company_type, scraped_date, workday_today
        )
        if _needs_detail(raw, job):
            needs_detail.append((len(jobs), path))
        jobs.append(job)

    details = _fetch_details(token, api, [path for _index, path in needs_detail])
    dropped: set[int] = set()
    for index, path in needs_detail:
        info = details[path]
        if info is None:
            dropped.add(index)
        else:
            jobs[index] = _with_detail(jobs[index], info)
    jobs = [job for index, job in enumerate(jobs) if index not in dropped]

    outside_scope = 0
    if location_scope:
        unscoped_count = len(jobs)
        jobs = [job for job in jobs if _location_in_scope(job.location, location_scope)]
        outside_scope = unscoped_count - len(jobs)

    scope_summary = (
        f", {outside_scope} outside {location_scope!r} scope"
        if location_scope
        else ""
    )
    logger.info(
        "workday[%s]: fetched %d jobs (%d detail lookup(s), %d failed%s)",
        token,
        len(jobs),
        len(needs_detail),
        len(dropped),
        scope_summary,
    )

    return jobs


def _location_in_scope(location: str, scope: str) -> bool:
    """Whether one normalized office in `location` exactly matches `scope`."""
    normalized_scope = normalize_location(scope).casefold()
    return any(
        office.strip().casefold() == normalized_scope
        for office in location.split("|")
        if office.strip()
    )


def _fetch_details(token: str, api: str, paths: list[str]) -> dict[str, dict | None]:
    """Fetch details; only confirmed 404/410 postings may be omitted."""
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        first_try = list(pool.map(lambda path: _fetch_detail(token, api, path), paths))
    details: dict[str, dict | None] = {}
    for path, (info, _error) in zip(paths, first_try):
        if info is None:
            info, error = _fetch_detail(token, api, path)
            if info is None:
                if not _posting_is_gone(error):
                    raise RuntimeError(
                        f"workday[{token}]: detail for {path} failed; "
                        "board snapshot is unsafe to prune"
                    ) from error
                logger.info(
                    "workday[%s]: posting %s closed before detail fetch", token, path
                )
        details[path] = info
    return details


def _posting_is_gone(error: Exception | None) -> bool:
    """Only a missing posting is safe to omit from an otherwise valid board."""
    return (
        isinstance(error, HTTPError)
        and error.response is not None
        and error.response.status_code in (404, 410)
    )


def _fetch_detail(token: str, api: str, path: str) -> tuple[dict | None, Exception | None]:
    try:
        response = get_with_retries("workday", token, f"{api}{path}")
        payload = response.json()
        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict) or not info:
            raise IncompleteBoardResponse(
                f"workday[{token}]: missing jobPostingInfo for {path}"
            )
        return info, None
    except Exception as exc:
        return None, exc


def _parse_token(token: str) -> tuple[str, str]:
    """Build the Workday host and API base URL from a board token."""
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
    workday_today: datetime.date,
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
        first_published=_posted_on_date(raw.get("postedOn"), workday_today),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, ""),
        company_type=company_type,
        source="Workday",
        description="",
        salary=None,
    )


def _needs_detail(raw: dict, job: Job) -> bool:
    """Return whether a relevant posting needs detail-page locations."""
    locations_text = raw.get("locationsText") or ""
    if locations_text and not _MULTI_LOCATION_RE.match(locations_text):
        return False
    return classify_job(job) is not None


def _with_detail(job: Job, info: dict) -> Job:
    """Apply exact detail-page locations and dates to a job."""
    entries = [info.get("location")] + list(info.get("additionalLocations") or [])
    location = normalize_locations(entries)
    if not location:
        location = normalize_locations(
            [(info.get("jobRequisitionLocation") or {}).get("descriptor")]
        )
    start_date = normalize_date(info.get("startDate"))
    return dataclasses.replace(
        job,
        location=location,
        first_published=start_date or job.first_published,
    )


def _workday_today(token: str, api: str, raw_jobs: list[dict]) -> datetime.date | None:
    """Infer Workday's current date from its relative posting text."""
    for raw in raw_jobs:
        days_ago = _days_ago(raw.get("postedOn"))
        if days_ago is None or not raw.get("externalPath"):
            continue
        info, error = _fetch_detail(token, api, raw["externalPath"])
        start_date = normalize_date((info or {}).get("startDate"))
        if not start_date:
            logger.warning(
                "workday[%s]: couldn't read Workday's current date (%s), "
                "counting \"Posted N Days Ago\" back from this machine's date",
                token,
                error or "no startDate",
            )
            return None
        return datetime.date.fromisoformat(start_date) + datetime.timedelta(days=days_ago)
    return None


def _posted_on_date(posted_on: str | None, workday_today: datetime.date) -> str:
    """Convert a Workday relative posting label to an ISO date."""
    days_ago = _days_ago(posted_on)
    if days_ago is None:
        return ""
    return normalize_date(workday_today - datetime.timedelta(days=days_ago))


def _days_ago(posted_on: str | None) -> int | None:
    text = (posted_on or "").strip().lower()
    if text == "posted today":
        return 0
    if text == "posted yesterday":
        return 1
    match = _POSTED_DAYS_AGO_RE.match(text)
    return int(match.group(1)) if match else None
