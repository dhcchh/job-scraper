"""Greenhouse Job Board API client.

One function: give it a board token, get back this run's Job objects.

`firm` (not `board_token`) is what ends up in Job.board -- the token is only
needed to hit the right Greenhouse URL. This keeps the sheet's board column
readable ("HRT", "Optiver") instead of the raw career-page slug
("wehrtyou", "optiverneurips") -- see company_toml/greenhouse_boards.toml
for the token->firm mapping. Because Job.board (not the token) is also half
of the (board, job_id) dedup/prune key in sheets.writer, changing what firm
maps to for an existing token would make every already-tracked job under
that token look new again next run -- rename firms there with care, and
re-key any already-written rows to match if you do. Every Job returned here
is stamped `source="Greenhouse"` -- a fixed literal, not anything read from
the response -- see models.Job's docstring.

Transient failures (connection errors, timeouts, 5xx, 429) are retried with
exponential backoff -- see job_boards._http.get_with_retries, the shared
retry helper every board client uses. Anything else (a 4xx other than 429)
raises immediately: retrying a bad request/board token can't possibly
succeed. Exhausting all retries still raises, same as before this existed --
main.py is what decides a failed board shouldn't abort the whole run (it
catches per-board and skips), not this module.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target
# tests/greenhouse/test_client.py's monkeypatch.setattr(client.requests,
# "get", ...) patches, since `requests` is the same module object wherever
# it's imported, so patching it here still reaches _http's call.
import requests

from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date, strip_html
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def fetch_greenhouse_jobs(
    board_token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Greenhouse board.

    `board_token` is the Greenhouse career-page slug used to hit the API;
    `firm` and `company_type` (both from company_toml/greenhouse_boards.toml)
    are stored as-is on every returned Job -- `firm` instead of the token
    (see the module docstring), `company_type` because it's a fact about the
    firm, not something derivable from a posting's title.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Greenhouse.

    We ask for content=true (full HTML job descriptions) -- `_to_job` runs
    that through models.strip_html into Job.description, but only for a
    firm in models.FIRMS_NEEDING_DESCRIPTION (confirmed to have an apply
    page that can't be trusted to actually show the JD -- see that
    constant's comment); every other firm's Job.description stays "".
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_token}/jobs"
    response = get_with_retries("greenhouse", board_token, url, params={"content": "true"})

    payload = response.json()
    raw_jobs = payload.get("jobs", [])

    # `id` is the one field _to_job doesn't already default via .get() --
    # every other field degrades to "" on a malformed record, so this
    # matches that same defensive posture rather than letting one bad
    # record (no id at all) raise and cost this whole board's ~hundreds of
    # otherwise-good postings for the day (main.py treats any exception
    # from fetch_greenhouse_jobs as the entire board failing, not per-job).
    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    if skipped:
        logger.warning(
            "greenhouse[%s]: skipped %d job(s) missing an id", board_token, skipped
        )
    logger.info("greenhouse[%s]: fetched %d jobs", board_token, len(jobs))

    return jobs


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = raw.get("title", "")
    department = _first_name(raw.get("departments"))
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("absolute_url", ""),
        updated_at=normalize_date(raw.get("updated_at", "")),
        first_published=normalize_date(raw.get("first_published", "")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Greenhouse",
        description=(
            strip_html(raw.get("content", ""))
            if firm in FIRMS_NEEDING_DESCRIPTION
            else ""
        ),
        # No structured pay field exists in this API at all -- confirmed
        # across Stripe/Airbnb/Affirm/GitLab/Robinhood's live boards. A pay
        # range, when a posting discloses one, is only ever freeform text
        # buried in `content` (e.g. Robinhood: "the salary range for this
        # role is ... aligned to one of 3 compensation zones"), in no
        # consistent shape worth regexing out -- see models.Job.salary.
        salary="",
    )


def _first_name(items: list[dict] | None) -> str:
    """First department name, or "" -- a posting can list several or none.

    TODO(filtering extension): if you need every department rather than
    just the first, this is the spot to change -- e.g. join them with a
    delimiter.
    """
    if not items:
        return ""
    return items[0].get("name", "")


def _location(raw: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    `raw["offices"]` (present because fetch_greenhouse_jobs always requests
    content=true) is Greenhouse's own *pre-split* per-office list -- each
    entry's `name` is one office, with none of the comma-inside-a-city-vs-
    comma-between-cities ambiguity `location.name` has (that field is one
    free-text string a recruiter typed, e.g. "London, United Kingdom; New
    York, NY, United States" -- not safely splittable by this code, only
    by Greenhouse's own admin UI). Falls back to `location.name`
    (stripped) only if `offices` is ever missing/empty.

    " | " (not ",") is the join delimiter deliberately: a comma can appear
    inside a single normalized name (none do today, but nothing guarantees
    that for every future city), so it can't double as the between-offices
    separator without reintroducing the exact ambiguity this function
    exists to avoid.
    """
    offices = raw.get("offices") or []
    names: list[str] = []
    for office in offices:
        name = normalize_location(office.get("name", ""))
        if name and name not in names:
            names.append(name)
    if names:
        return " | ".join(names)
    return normalize_location((raw.get("location") or {}).get("name", ""))
