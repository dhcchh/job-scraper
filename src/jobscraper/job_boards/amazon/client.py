"""Amazon careers-site (amazon.jobs) client.

amazon.jobs has no public API. Its own search page calls `/en/search.json`,
an undocumented, unauthenticated JSON endpoint; this client uses the same
endpoint. Each board token is one Amazon job category slug (for example
"software-development"), and every posting belongs to exactly one category.
"""

import datetime
import functools
import json
import logging
import urllib.parse

from jobscraper.board_config import PROVIDER_LABELS
from jobscraper.enrichment.classify_region import names_a_country
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_locations, normalize_office
from jobscraper.job_boards._http import IncompleteBoardResponse, paginate_with_overlap
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job, normalize_job_id, strip_html

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.amazon.jobs"
_SEARCH_URL = f"{_BASE_URL}/en/search.json"

# The largest page the endpoint serves; a larger result_limit returned no
# jobs (checked 2026-11-08).
_PAGE_SIZE = 100

# Postings are sorted newest first, so one added during a fetch shifts every
# later page, and one closed shifts the pages after it. Each page repeats
# this many of the previous page's postings to stitch across such a shift.
_PAGE_OVERLAP = 10

# The endpoint reports at most this many hits and returns no jobs at or past
# this offset, so a category this large cannot be fetched completely.
_HIT_CAP = 10_000


def fetch_amazon_jobs(
    category: str,
    firm: str,
    company_type: str,
    *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Fetch and normalize every open Amazon posting in one job category."""
    if scraped_date is None:
        scraped_date = datetime.date.today()

    query = urllib.parse.urlencode({"category[]": category, "sort": "recent"})
    raw_jobs = paginate_with_overlap(
        "amazon",
        category,
        f"{_SEARCH_URL}?{query}",
        cursor_param="offset",
        page_size_param="result_limit",
        page_size=_PAGE_SIZE,
        overlap=_PAGE_OVERLAP,
        total_key="hits",
        items_key="jobs",
        id_key="id_icims",
        check_first_page=functools.partial(_check_first_page, category=category),
    )
    if not raw_jobs:
        logger.warning("amazon[%s]: the category lists no postings", category)

    jobs = [_to_job(raw, firm, company_type, scraped_date) for raw in raw_jobs]
    logger.info("amazon[%s]: fetched %d jobs", category, len(jobs))
    return jobs


def _check_first_page(payload: dict, *, category: str) -> None:
    """Fail before paging if the category can't be fetched completely.

    An empty result is a valid snapshot only for a slug the endpoint knows:
    an unknown or renamed slug also returns zero hits, not an error, and
    would otherwise look like every posting in it closed.
    """
    hits = payload["hits"]  # an int; the paginator validated it
    if hits >= _HIT_CAP:
        raise IncompleteBoardResponse(
            f"amazon[{category}]: {hits} postings reach the endpoint's "
            f"{_HIT_CAP}-result cap, so the category can't be fetched completely"
        )
    if hits == 0 and not _is_known_category(payload, category):
        raise IncompleteBoardResponse(
            f"amazon[{category}]: no postings and the endpoint doesn't "
            "recognise the category slug"
        )


def _is_known_category(payload: dict, category: str) -> bool:
    """Whether the endpoint recognised the category slug.

    The response echoes the search it ran as a JSON string, with a known
    slug replaced by the category's name ("research-science" becomes
    "Research Science"); an unknown slug is echoed unchanged (checked
    2026-11-14). An echo that can't be read counts as unknown.
    """
    try:
        request = json.loads(payload["job_posting_search_request"])
        names = [
            value["name"]
            for facet in request["jobPostingSearchRequest"]["filterFacets"]
            if facet["name"] == "category"
            for value in facet["values"]
        ]
    except (KeyError, TypeError, ValueError):
        return False
    return bool(names) and category not in names


def _to_job(
    raw: dict, firm: str, company_type: str, scraped_date: datetime.date
) -> Job:
    job_id = normalize_job_id(raw["id_icims"])
    title = raw.get("title") or ""
    department = raw.get("job_family") or ""
    return Job(
        board=firm,
        job_id=job_id,
        title=title,
        department=department,
        location=_location(raw, job_id),
        url=_url(raw, job_id),
        updated_at="",
        first_published=_posted_date(raw.get("posted_date")),
        scraped_date=scraped_date,
        seniority=classify_seniority(title, department),
        company_type=company_type,
        source=PROVIDER_LABELS["amazon"],
        description=_description(raw) if firm in FIRMS_NEEDING_DESCRIPTION else "",
        salary=None,
    )


def _url(raw: dict, job_id: str) -> str:
    """Link the posting's own page, which carries the apply button."""
    path = raw.get("job_path") or ""
    if path.startswith("/"):
        return f"{_BASE_URL}{path}"
    return f"{_BASE_URL}/en/jobs/{job_id}"


def _posted_date(value: object) -> str:
    """Convert "November  6, 2026" (note the padded day) to an ISO date."""
    if not value or not isinstance(value, str):
        return ""
    try:
        return datetime.datetime.strptime(" ".join(value.split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _description(raw: dict) -> str:
    """Combine the description and both qualification lists as plain text."""
    fields = ("description", "basic_qualifications", "preferred_qualifications")
    parts = (strip_html(raw.get(field) or "") for field in fields)
    return "\n\n".join(part for part in parts if part)


def _location(raw: dict, job_id: str) -> str:
    """Normalize and join every listed office.

    `normalized_location` ends in an ISO alpha-3 code ("Hyderabad, Telangana,
    IND", or just "SGP"), so offices come from the structured `locations`
    entries instead.
    """
    location = normalize_locations([_office(entry) for entry in _location_entries(raw, job_id)])
    return location or normalize_office(raw.get("city") or "", raw.get("country_code") or "")


def _location_entries(raw: dict, job_id: str) -> list[dict]:
    """Decode `locations`, a list of JSON-encoded objects.

    A malformed entry is skipped rather than failing the category: an office
    isn't part of a posting's identity, and `_location` falls back to the
    top-level city and country when no entry is usable.
    """
    items = raw.get("locations") or []
    if not isinstance(items, list):
        logger.warning("amazon job %s: skipping malformed locations", job_id)
        return []
    entries = []
    for item in items:
        try:
            entry = json.loads(item) if isinstance(item, str) else item
        except json.JSONDecodeError:
            entry = None
        if not isinstance(entry, dict):
            logger.warning("amazon job %s: skipping a malformed location entry", job_id)
            continue
        entries.append(entry)
    return entries


def _office(entry: dict) -> str:
    """One office, preferring the shared ISO alpha-3 table over Amazon's name.

    Amazon's country names include "People's Republic of China" and "Hong
    Kong SAR", which the region rules don't know; its codes cover those.
    Codes missing from the shared table fall back to the country name.
    """
    city = _city(entry)
    office = normalize_office(city, entry.get("countryIso3a") or "")
    if names_a_country(office):
        return office
    return normalize_office(city, entry.get("normalizedCountryName") or "") or office


def _city(entry: dict) -> str:
    """Use Amazon's normalized city only to fix capitals ("SEATTLE").

    Its other substitutions can name a different place ("Arizona" becomes
    "Arizona City"), so the posted city is kept otherwise.
    """
    city = entry.get("city") or ""
    normalized = entry.get("normalizedCityName") or ""
    if normalized and normalized.casefold() == city.casefold():
        return normalized
    return city
