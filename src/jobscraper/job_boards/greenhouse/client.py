"""Greenhouse Job Board API client.

One function: give it a board token, get back this run's Job objects.

`firm` (not `board_token`) is what ends up in Job.board -- the token is only
needed to hit the right Greenhouse URL. This keeps the sheet's board column
readable ("HRT", "Optiver") instead of the raw career-page slug
("wehrtyou", "optiverneurips") -- see greenhouse_boards.toml for the
token->firm mapping. Because Job.board (not the token) is also half of the
(board, job_id) dedup/prune key in sheets.writer, changing what firm maps to
for an existing token would make every already-tracked job under that token
look new again next run -- rename firms there with care, and re-key any
already-written rows to match if you do.

TODO(retries): no retry/backoff here -- a transient network blip or a
Greenhouse 5xx fails the whole run (see main.py, which doesn't catch this
per board either). Fine for a personal daily job at this scale; add a retry
loop here if it turns out to be flaky in practice.
"""

from __future__ import annotations

import datetime
import logging

import requests

from jobscraper.models import Job
from jobscraper.classifications.classify_seniority import classify_seniority

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def fetch_jobs(
    board_token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Greenhouse board.

    `board_token` is the Greenhouse career-page slug used to hit the API;
    `firm` and `company_type` (both from greenhouse_boards.toml) are stored
    as-is on every returned Job -- `firm` instead of the token (see the
    module docstring), `company_type` because it's a fact about the firm,
    not something derivable from a posting's title.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Greenhouse.

    We ask for content=true (full HTML job descriptions) even though today's
    Job model doesn't keep that text -- it's the natural place a future
    keyword-filtering extension would read from, and it's free to request
    now.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_token}/jobs"
    response = requests.get(url, params={"content": "true"}, timeout=30)
    response.raise_for_status()  # no retry loop -- see module docstring

    payload = response.json()
    raw_jobs = payload.get("jobs", [])
    logger.info("greenhouse[%s]: fetched %d jobs", board_token, len(raw_jobs))

    return [_to_job(raw, firm, company_type, scraped_date) for raw in raw_jobs]


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
        location=(raw.get("location") or {}).get("name", ""),
        url=raw.get("absolute_url", ""),
        updated_at=raw.get("updated_at", ""),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
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
