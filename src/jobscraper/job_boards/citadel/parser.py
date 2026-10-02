"""Fail-closed extraction of Citadel's public listing HTML."""

import dataclasses
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from jobscraper.job_boards._http import IncompleteBoardResponse

LISTING_URL = "https://www.citadel.com/careers/open-opportunities/"
_DETAIL_PATH = re.compile(r"^/careers/details/([a-zA-Z0-9_-]+)/?$")
_PAGE_PATH = re.compile(r"^/careers/open-opportunities/(?:page/([1-9][0-9]*)/)?$")
_COUNTER = re.compile(r"\bViewing\s+([\d,]+)\s+of\s+([\d,]+)\b", re.I)
# Citadel lists offices as city names, rather than city/state/country triples.
# Only split a comma-separated label if EVERY component is a recognized office.
# An unfamiliar label must fail the board until its meaning is verified.
_OFFICES = frozenset({
    "Boston", "Brisbane", "Calgary", "Chicago", "Dallas", "Dublin", "Greenwich",
    "Hamburg", "Hong Kong", "Houston", "London", "Miami", "New York", "Paris",
    "San Francisco", "Shanghai", "Singapore", "Sydney", "Tokyo", "Washington D.C.",
    "Wellington", "Zurich",
})


@dataclasses.dataclass(frozen=True, slots=True)
class Listing:
    job_id: str
    title: str
    offices: tuple[str, ...]
    url: str


@dataclasses.dataclass(frozen=True, slots=True)
class ListingPage:
    jobs: tuple[Listing, ...]
    shown: int
    total: int
    next_page: int | None


def _text(element: Tag) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _detail_url(href: str) -> tuple[str, str]:
    url = urlsplit(urljoin(LISTING_URL, href))
    match = _DETAIL_PATH.fullmatch(url.path)
    if (
        url.scheme != "https" or url.netloc != "www.citadel.com" or not match
    ):
        raise IncompleteBoardResponse("citadel: invalid detail URL")
    job_id = match[1]
    return job_id, f"https://www.citadel.com/careers/details/{job_id}/"


def _office_labels(card: Tag) -> tuple[str, ...]:
    offices: list[str] = []
    containers: list[Tag] = []
    for element in card.find_all(["p", "span", "div", "li"]):
        if element.find_parent(["h2", "h3", "h4", "h5"]):
            continue
        if any(parent is container for parent in element.parents for container in containers):
            continue
        value = _text(element)
        if not value or value.casefold() in {"apply", "apply now"}:
            continue
        parts = tuple(part.strip() for part in value.split(","))
        if parts and all(part in _OFFICES for part in parts):
            offices.extend(parts)
            containers.append(element)
        elif not element.find(["p", "span", "div", "li", "h2", "h3", "h4", "h5"]):
            raise IncompleteBoardResponse("citadel: unfamiliar office label in job card")
    if not offices:
        raise IncompleteBoardResponse("citadel: missing or unfamiliar office labels")
    return tuple(dict.fromkeys(offices))


def _listing(link: Tag) -> Listing:
    job_id, url = _detail_url(str(link.get("href", "")))
    card = link
    # A card may wrap its title in a link, or itself be one large link.
    # Stop before any ancestor that contains another posting.
    while isinstance(card, Tag):
        heading = card.find(["h2", "h3", "h4", "h5"])
        if heading is not None and _text(heading):
            try:
                offices = _office_labels(card)
            except IncompleteBoardResponse:
                pass
            else:
                return Listing(job_id, _text(heading), offices, url)
        parent = card.parent
        if not isinstance(parent, Tag):
            break
        if len(parent.select('a[href*="/careers/details/"]')) != 1:
            break
        card = parent
    raise IncompleteBoardResponse("citadel: missing title or office labels in job card")


def parse_listing_page(html: str) -> ListingPage:
    """Extract counters, cards and next links without accepting partial content.

    Structural selectors deliberately avoid guessing site-specific CSS classes.
    Live CI probes must verify them against successfully retrieved listing HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.body or soup
    counters = {
        (int(shown.replace(",", "")), int(total.replace(",", "")))
        for shown, total in _COUNTER.findall(root.get_text(" ", strip=True))
    }
    if len(counters) != 1:
        raise IncompleteBoardResponse("citadel: missing or inconsistent listing total")
    shown, total = counters.pop()
    if shown > total:
        raise IncompleteBoardResponse("citadel: displayed count exceeds total")
    jobs = tuple(_listing(link) for link in root.select('a[href*="/careers/details/"]'))
    if len({job.job_id for job in jobs}) != len(jobs):
        raise IncompleteBoardResponse("citadel: duplicate job identities on page")
    next_pages = set()
    for link in root.find_all("a", href=True):
        label = " ".join([
            _text(link), str(link.get("aria-label", "")),
            " ".join(link.get("rel", [])), " ".join(link.get("class", [])),
        ])
        if not re.search(r"\bnext\b", label, re.I):
            continue
        url = urlsplit(urljoin(LISTING_URL, str(link["href"])))
        match = _PAGE_PATH.fullmatch(url.path)
        if (
            url.scheme != "https" or url.netloc != "www.citadel.com"
            or not match or url.query or url.fragment
        ):
            raise IncompleteBoardResponse("citadel: invalid next-page link")
        next_pages.add(int(match[1] or 1))
    if len(next_pages) > 1:
        raise IncompleteBoardResponse("citadel: conflicting next-page links")
    return ListingPage(jobs, shown, total, next(iter(next_pages), None))
