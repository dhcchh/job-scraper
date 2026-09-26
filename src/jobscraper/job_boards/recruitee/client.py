"""Recruitee Careers Site API client.

One function: give it a company's Recruitee subdomain, get back this run's
Job objects -- same shape as the other board clients, adapted to
Recruitee's response:

- `GET https://<subdomain>.recruitee.com/api/offers/` returns
  {"offers": [...]}, every published posting in one response (no
  pagination). A company that serves its careers site from its own domain
  (e.g. OGP's careers.open.gov.sg) still answers on its recruitee.com
  subdomain, which is what `token` (company_toml/recruitee_boards.toml) is.
- Unlike Lever/Ashby/SmartRecruiters, both timestamps are real:
  `published_at` ("went live") and `updated_at` (differs from
  `published_at` on 85 of 94 postings across OGP's, Mathrix's and Aikido
  Security's live boards), so first_published and updated_at are both
  populated, same as Greenhouse/Eightfold. Both are "YYYY-MM-DD HH:MM:SS
  UTC" strings, which models.normalize_date already reads.
- `locations[]` is Recruitee's own pre-split per-office list, each entry
  with its own `city`/`country` -- `_location` normalizes just each
  `city`, same as SmartRecruiters' client, since normalize_location.py's
  table is keyed by bare cities. Falls back to the posting's top-level
  `city` if `locations` is ever empty. A remote posting still carries a
  city (e.g. Aikido's "Remote job" in Paris), so there's no separate
  remote handling here.
- `department` is a plain top-level string.
- `careers_url` (the posting's page on the careers site) is Job.url, not
  `careers_apply_url` (straight to the application form) -- matching
  absolute_url/jobUrl's role in the other clients.
- `description` and `requirements` are separate HTML fields; for a firm in
  models.FIRMS_NEEDING_DESCRIPTION both go through models.strip_html, the
  same way Greenhouse's `content` does. No Recruitee firm is in that set
  today.
- Every posting carries a `salary` object ({min, max, period, currency}),
  but it was all-null on every one of those 94 live postings, so what a
  populated one looks like is unconfirmed -- Job.salary stays None rather
  than formatting a shape never actually seen.

`firm` (not the subdomain) is what ends up in Job.board, for the same
readability and dedup-key reasons documented in
job_boards/greenhouse/client.py. Every Job returned here is stamped
`source="Recruitee"` -- a fixed literal, not anything read from the
response -- see models.Job's docstring.

Retries via job_boards._http.get_with_retries, the same shared retry policy
every board client uses -- see that module's docstring.
"""

from __future__ import annotations

import datetime
import logging

# Not called directly here (job_boards._http.get_with_retries does the
# actual GET) -- kept as the target tests/recruitee/test_client.py's
# monkeypatch.setattr(client.requests, "get", ...) patches, same as the
# other clients.
import requests

from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_location
from jobscraper.job_boards._http import get_with_retries
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_date, strip_html

logger = logging.getLogger(__name__)

URL_TEMPLATE = "https://{subdomain}.recruitee.com/api/offers/"


def fetch_recruitee_jobs(
    subdomain: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch every open job posted on one company's Recruitee careers site.

    `subdomain` is the company's recruitee.com subdomain (e.g.
    "opengovernmentproducts"); `firm` and `company_type` (both from
    company_toml/recruitee_boards.toml) are stored as-is on every returned
    Job, same as the other clients.

    `scraped_date` defaults to today and is stamped on every Job returned --
    it's what sheets.writer prunes on later, not anything from Recruitee.
    """
    if scraped_date is None:
        scraped_date = datetime.date.today()

    url = URL_TEMPLATE.format(subdomain=subdomain)
    response = get_with_retries("recruitee", subdomain, url)

    raw_jobs = response.json().get("offers", [])

    # Same defensive posture as the other clients: one malformed record (no
    # id at all) is skipped rather than costing the whole board.
    jobs = []
    skipped = 0
    for raw in raw_jobs:
        if "id" not in raw:
            skipped += 1
            continue
        jobs.append(_to_job(raw, firm, company_type, scraped_date))
    if skipped:
        logger.warning(
            "recruitee[%s]: skipped %d job(s) missing an id", subdomain, skipped
        )
    logger.info("recruitee[%s]: fetched %d jobs", subdomain, len(jobs))

    return jobs


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    title = (raw.get("title") or "").strip()
    department = (raw.get("department") or "").strip()
    return Job(
        board=firm,
        job_id=str(raw["id"]),
        title=title,
        department=department,
        location=_location(raw),
        url=raw.get("careers_url") or "",
        updated_at=normalize_date(raw.get("updated_at") or ""),
        first_published=normalize_date(raw.get("published_at") or ""),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source="Recruitee",
        description=_description(raw) if firm in FIRMS_NEEDING_DESCRIPTION else "",
        salary=None,
    )


def _description(raw: dict) -> str:
    """The JD as plain text: `description` then `requirements`, each run
    through models.strip_html, blank ones skipped."""
    parts = (strip_html(raw.get(field) or "") for field in ("description", "requirements"))
    return "\n\n".join(part for part in parts if part)


def _location(raw: dict) -> str:
    """Every office this posting is open in, normalized and " | "-joined --
    see the module docstring for why just each entry's `city`."""
    names: list[str] = []
    for entry in raw.get("locations") or []:
        normalized = normalize_location(entry.get("city") or "")
        if normalized and normalized not in names:
            names.append(normalized)
    if names:
        return " | ".join(names)
    return normalize_location(raw.get("city") or "")
