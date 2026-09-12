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

Transient failures (connection errors, timeouts, 5xx, 429) are retried with
exponential backoff -- see _get_with_retries. Anything else (a 4xx other
than 429) raises immediately: retrying a bad request/board token can't
possibly succeed. Exhausting all retries still raises, same as before this
existed -- main.py is what decides a failed board shouldn't abort the whole
run (it catches per-board and skips), not this module.
"""

from __future__ import annotations

import datetime
import logging
import time

import requests

from jobscraper.models import Job
from jobscraper.classifications.classify_seniority import classify_seniority

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

# 3 attempts total (1 initial + 2 retries), 2s/4s backoff between them --
# enough to ride out a brief network blip or a Greenhouse hiccup without
# stalling a daily run for long if it's not going to recover.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


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
    response = _get_with_retries(board_token, url, params={"content": "true"})

    payload = response.json()
    raw_jobs = payload.get("jobs", [])
    logger.info("greenhouse[%s]: fetched %d jobs", board_token, len(raw_jobs))

    return [_to_job(raw, firm, company_type, scraped_date) for raw in raw_jobs]


def _get_with_retries(board_token: str, url: str, *, params: dict) -> requests.Response:
    """GET url, retrying transient failures with exponential backoff.

    Retries a connection error/timeout (no response at all) or an HTTPError
    whose response is a 5xx or 429 (server trouble/rate-limited -- worth
    trying again) up to _MAX_ATTEMPTS times total. Any other HTTPError (a
    4xx like a bad/renamed board token) raises immediately: the request
    itself is wrong, so retrying it changes nothing.
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
                "greenhouse[%s]: attempt %d/%d failed (%s), retrying in %.0fs",
                board_token,
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
