"""Route a job to its sheet tab, the same way for every command."""

from typing import NamedTuple

from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    is_excluded,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.models import Job

# Every managed job tab, by category and then region.
JOB_TABS: tuple[str, ...] = tuple(
    tab_name(category, region)
    for category, _keywords in CATEGORIES
    for region in REGIONS
)


class Route(NamedTuple):
    """Where a job belongs.

    `tab` is None for a job the sheet doesn't track: noise, which has no
    `category` either, or a job whose every office is in an excluded country.
    """

    tab: str | None
    category: str | None


def route_job(job: Job) -> Route:
    """Return the tab the daily scrape appends a job to, if any."""
    category = classify_job(job)
    if category is None or is_excluded(job.location):
        return Route(None, category)
    return Route(tab_name(category, classify_region(job.location)), category)
