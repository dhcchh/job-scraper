"""Lever Postings API client.

One function: give it a board slug, get back this run's Job objects -- same
shape as job_boards/greenhouse/client.py, adapted to Lever's response shape:

- The endpoint returns a bare JSON array of postings, not {"jobs": [...]}.
- A posting's title is `text`, not `title`; its apply link is `hostedUrl`,
  not `absolute_url`.
- Per-office locations live at `categories.allLocations` (Lever's own
  pre-split list, same role as Greenhouse's `offices[]` -- see that
  module's `_location` for why a pre-split list matters), with
  `categories.location` as the single-string fallback.
- A posting's org unit is `categories.department` on most boards, but some
  (confirmed on Palantir) omit `department` entirely and only set
  `categories.team` -- `_department` falls back to `team` so those boards
  don't lose the signal classify_seniority reads it for.
- There is no edited-at timestamp in this API at all (confirmed against the
  live schema across every board this project fetches) -- only `createdAt`,
  a "went live" signal, so unlike Greenhouse's client this stamps
  first_published from it and leaves updated_at "" rather than fabricating
  a value the source data doesn't have.
- The JD comes back already as plain text (`openingPlain`,
  `descriptionBodyPlain`, `additionalPlain`), not HTML like Greenhouse's
  `content` -- `_description` assembles Job.description straight from
  those instead of running Greenhouse's strip_html() path, except for
  `lists` (responsibilities/requirements), which only has an HTML
  `content` per item. Same as every client, this only runs for a firm in
  models.FIRMS_NEEDING_DESCRIPTION; no live Lever board is in that set
  today.

`firm` (not the slug) is what ends up in Job.board, for the same readability
and dedup-key reasons documented in job_boards/greenhouse/client.py. Every
Job returned here is stamped `source="Lever"` -- a fixed literal, not
anything read from the response -- see models.Job's docstring.

Retries the same way as the Greenhouse client (transient failures, 3
attempts, exponential backoff) -- see that module's docstring for the
reasoning, unchanged here.
"""

from __future__ import annotations

import datetime
import logging
import time

import requests

from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date, strip_html
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"

# Same reasoning and values as job_boards/greenhouse/client.py.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


def fetch_lever_jobs(
    board_slug: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Lever site.

    `board_slug` is the Lever site slug used to hit the API (e.g.
    "palantir"); `firm` and `company_type` (both from
    company_toml/lever_boards.toml) are stored as-is on every returned Job,
    same as the Greenhouse client.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Lever.

    `mode=json` is required -- without it Lever serves an HTML careers page
    instead of JSON.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = f"{BASE_URL}/{board_slug}"
    response = _get_with_retries(board_slug, url, params={"mode": "json"})

    raw_jobs = response.json()

    # Same defensive posture as the Greenhouse client's fetch_greenhouse_jobs: one
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
            "lever[%s]: skipped %d job(s) missing an id", board_slug, skipped
        )
    logger.info("lever[%s]: fetched %d jobs", board_slug, len(jobs))

    return jobs


def _get_with_retries(board_slug: str, url: str, *, params: dict) -> requests.Response:
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
                "lever[%s]: attempt %d/%d failed (%s), retrying in %.0fs",
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
    title = raw.get("text", "")
    categories = raw.get("categories") or {}
    department = _department(categories)
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(categories),
        url=raw.get("hostedUrl", ""),
        updated_at="",
        first_published=normalize_date(_created_at(raw)),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Lever",
        description=_description(raw) if firm in FIRMS_NEEDING_DESCRIPTION else "",
    )


def _department(categories: dict) -> str:
    """Org-unit label for this posting, or "" -- see the module docstring
    for why `team` is a real fallback here, not just a nice-to-have."""
    return categories.get("department") or categories.get("team") or ""


def _location(categories: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined.

    `categories.allLocations` is Lever's own pre-split per-office list, the
    same role `offices[]` plays for Greenhouse (see that client's
    `_location`) -- falls back to the single `categories.location` string
    only when `allLocations` is empty/missing.
    """
    names: list[str] = []
    for name in categories.get("allLocations") or []:
        normalized = normalize_location(name)
        if normalized and normalized not in names:
            names.append(normalized)
    if names:
        return " | ".join(names)
    return normalize_location(categories.get("location") or "")


def _description(raw: dict) -> str:
    """The full JD as plain text, assembled from Lever's own plain-text
    fields rather than stripping the HTML `description` field itself.

    Unlike Greenhouse, Lever's API already hands back plain text for most
    of the posting -- `openingPlain`/`descriptionPlain` (company intro, the
    two are identical on every posting checked) and `descriptionBodyPlain`
    (the actual role-specific body) -- so this only reaches for strip_html
    on `lists` (responsibilities/requirements sections), which has no plain
    variant, only each item's HTML `content`. Sections are joined with blank
    lines in the same opening -> body -> lists -> closing order Lever's own
    hosted page renders them in; any missing/empty section is skipped
    rather than leaving a stray blank line.
    """
    sections = [
        (raw.get("openingPlain") or "").strip(),
        (raw.get("descriptionBodyPlain") or "").strip(),
    ]
    for item in raw.get("lists") or []:
        header = (item.get("text") or "").strip()
        body = strip_html(item.get("content") or "")
        sections.append("\n".join(part for part in (header, body) if part))
    sections.append((raw.get("additionalPlain") or "").strip())
    return "\n\n".join(section for section in sections if section)


def _created_at(raw: dict) -> datetime.date | None:
    """`createdAt` (epoch milliseconds, UTC) as a calendar date, or None.

    Only a calendar date is ever kept (via normalize_date), so the exact
    time of day -- and the UTC-vs-local distinction it would otherwise
    raise -- doesn't matter here.
    """
    epoch_ms = raw.get("createdAt")
    if not epoch_ms:
        return None
    return datetime.datetime.fromtimestamp(
        epoch_ms / 1000, tz=datetime.timezone.utc
    ).date()
