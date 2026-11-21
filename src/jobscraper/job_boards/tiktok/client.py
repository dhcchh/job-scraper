"""TikTok careers-site (lifeattiktok.com) client.

lifeattiktok.com has no documented API. Its search page posts to
`api.lifeattiktok.com/api/v1/public/supplier/search/job/posts`, an
unauthenticated JSON endpoint that answers HTTP 400 unless the request
carries a `website-path: tiktok` header; this client uses the same endpoint.
A board token lists the site's city codes, comma-separated: "CT_163" is
Singapore.

One request returns the whole listing. The endpoint honours a page size far
above any city's postings (1,823 rows in one response, checked 2026-11-21),
so there is no paging for a posting added mid-fetch to shift. A list shorter
than the response's `count` fails the board.

Each posting names one city. The listing has no posting or update date, and
its salary fields were empty on every Singapore posting checked.
"""

import datetime
import logging

from jobscraper.enrichment.normalize_location import normalize_office
from jobscraper.job_boards.common.http import (
    IncompleteBoardResponse,
    post_with_retries,
    require_job_list,
)
from jobscraper.job_boards.common.jobs import build_job, jobs_from
from jobscraper.models import Job

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
_HEADERS = {"website-path": "tiktok"}
_JOB_URL = "https://lifeattiktok.com/search/{id}"

# Far above any city's postings, so one response is the whole listing.
_LIMIT = 3000

# A city_info parent chain runs city -> state -> country.
_COUNTRY_LEVEL = 1


def fetch_tiktok_jobs(
    token: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize every open TikTok posting in the token's cities."""
    cities = token.split(",")
    response = post_with_retries(
        "tiktok",
        token,
        _SEARCH_URL,
        json={"location_code_list": cities, "keyword": "", "limit": _LIMIT, "offset": 0},
        headers=_HEADERS,
    )
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise IncompleteBoardResponse(
            f"tiktok[{token}]: search failed: {_describe(payload)}"
        )
    data = payload.get("data")
    raw_jobs = require_job_list(data, "tiktok", token, key="job_post_list")
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(raw_jobs):
        raise IncompleteBoardResponse(
            f"tiktok[{token}]: {len(raw_jobs)} posting(s) returned of count {count!r}"
        )
    in_scope = [raw for raw in raw_jobs if _city_code(raw) in cities]
    if len(in_scope) < len(raw_jobs):
        logger.info(
            "tiktok[%s]: dropped %d posting(s) outside the token's cities",
            token,
            len(raw_jobs) - len(in_scope),
        )
    return jobs_from("tiktok", token, in_scope, _to_job, firm, company_type, scraped_date)


def _describe(payload: object) -> str:
    if not isinstance(payload, dict):
        return "response is not an object"
    return f"code {payload.get('code')!r}, message {payload.get('message')!r}"


def _city_code(raw: dict) -> str | None:
    return (raw.get("city_info") or {}).get("code")


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    return build_job(
        "tiktok",
        firm,
        company_type,
        scraped_date,
        job_id=raw["id"],
        title=(raw.get("title") or "").strip(),
        department=_name(raw.get("job_category")),
        location=_location(raw.get("city_info") or {}),
        url=_JOB_URL.format(id=raw["id"]),
        # "Intern", or "Regular" with a subject such as "Bachelor/Master
        # Graduate - 2027 Start": a seniority fallback for a title without one.
        employment_type=" ".join(
            name
            for name in (_name(raw.get("recruit_type")), _name(raw.get("job_subject")))
            if name
        ),
        description=lambda: "\n\n".join(
            text.strip()
            for text in (raw.get("description"), raw.get("requirement"))
            if text and text.strip()
        ),
    )


def _name(entry: object) -> str:
    return ((entry or {}).get("en_name") or "").strip() if isinstance(entry, dict) else ""


def _location(city_info: dict) -> str:
    """Name the city and the country at the top of its parent chain."""
    country = ""
    node = city_info.get("parent")
    while isinstance(node, dict):
        if node.get("location_type") == _COUNTRY_LEVEL:
            country = _name(node)
            break
        node = node.get("parent")
    return normalize_office(_name(city_info), country)
