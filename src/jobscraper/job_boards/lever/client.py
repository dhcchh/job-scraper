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
- A posting has `salaryRange` (`{min, max, currency, interval}`) only when
  that employer opted in to Lever's own pay-transparency feature --
  confirmed present on Belvedere Trading's live board (`{'min': 55000,
  'max': 65000, 'currency': 'USD', 'interval': 'per-year-salary'}`), absent
  on every other Lever board this project fetches. `_salary` formats it as
  e.g. "USD 55,000-65,000/year"; unlike `description`, this is NOT gated to
  a firm allowlist -- it's populated whenever a posting itself discloses a
  range, "" otherwise, same as models.Job.salary's own docstring explains.

`firm` (not the slug) is what ends up in Job.board, for the same readability
and dedup-key reasons documented in job_boards/greenhouse/client.py. Every
Job returned here is stamped `source="Lever"` -- a fixed literal, not
anything read from the response -- see models.Job's docstring.

Retries via job_boards._http.get_with_retries, the same shared retry policy
(transient failures, 3 attempts, exponential backoff) every board client
uses -- see that module's docstring for the reasoning.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here anymore (job_boards._http.get_with_retries does
# the actual GET) -- kept as the target tests/lever/test_client.py's
# monkeypatch.setattr(client.requests, "get", ...) patches, since `requests`
# is the same module object wherever it's imported, so patching it here
# still reaches _http's call.
import requests

from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date, strip_html
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings"


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
    response = get_with_retries("lever", board_slug, url, params={"mode": "json"})

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
        salary=_salary(raw),
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


def _salary(raw: dict) -> str:
    """A human-readable pay range from `salaryRange`, or "" if absent.

    `min`/`max` are plain numbers (not cents), `currency` is an ISO code
    (e.g. "USD") -- see the module docstring for the one confirmed real
    shape. Prefixes the currency code rather than guessing a symbol (a "$"
    is wrong for most non-USD currencies), and collapses to a single amount
    when min == max instead of e.g. "55,000-55,000". `interval` (e.g.
    "per-year-salary") is reduced to just "year" by _interval_label for a
    shorter cell; an interval shaped differently than that is shown as-is
    rather than mangled by a pattern that doesn't apply to it.
    """
    salary_range = raw.get("salaryRange")
    if not salary_range:
        return ""
    minimum = salary_range.get("min")
    maximum = salary_range.get("max")
    if minimum is None or maximum is None:
        return ""
    amount = f"{minimum:,}" if minimum == maximum else f"{minimum:,}-{maximum:,}"
    text = " ".join(part for part in (salary_range.get("currency", ""), amount) if part)
    interval = _interval_label(salary_range.get("interval", ""))
    return f"{text}/{interval}" if interval else text


def _interval_label(interval: str) -> str:
    if interval.startswith("per-") and interval.endswith("-salary"):
        return interval[len("per-") : -len("-salary")]
    return interval


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
