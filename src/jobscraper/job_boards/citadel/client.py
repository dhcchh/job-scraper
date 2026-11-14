"""Browser-backed Citadel listings, scoped to Singapore and Hong Kong."""

import asyncio
import dataclasses
import datetime
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

from jobscraper.board_config import PROVIDER_LABELS
from jobscraper.enrichment.classify_seniority import classify_seniority
from jobscraper.enrichment.normalize_location import normalize_locations
from jobscraper.job_boards._http import IncompleteBoardResponse
from jobscraper.job_boards.citadel.parser import (
    LISTING_URL, Listing, ListingPage, parse_listing_page,
)
from jobscraper.models import Job

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 300
CHALLENGE_TIMEOUT_MS = 45_000
NAVIGATION_INTERVAL_SECONDS = 10
_READY_SCRIPT = """() => {
    const match = document.body?.innerText.match(/Viewing\\s+([\\d,]+)\\s+of\\s+([\\d,]+)/i);
    if (!match) return false;
    return match[2].replaceAll(',', '') === '0' ||
        !!document.querySelector('a[href*="/careers/details/"]');
}"""


class CitadelChallengeError(RuntimeError):
    """Cloudflare did not grant automatic access to the listing."""


class CitadelNavigationError(RuntimeError):
    """A transient browser/navigation error interrupted the snapshot."""


class CitadelBrowserError(RuntimeError):
    """Chromium could not start or the overall fetch deadline expired."""


@dataclasses.dataclass(frozen=True, slots=True)
class CitadelSnapshot:
    jobs: tuple[Job, ...]
    global_count: int
    page_count: int


def _playwright():
    from playwright.async_api import Error, TimeoutError as PlaywrightTimeout, async_playwright

    return async_playwright(), PlaywrightTimeout, Error


def fetch_citadel_jobs(
    token: str, firm: str, company_type: str, *,
    scraped_date: datetime.date | None = None,
) -> list[Job]:
    """Match the shared provider interface; failures never become empty feeds."""
    if token != "citadel":
        raise ValueError("citadel: token must be 'citadel'")
    return list(fetch_citadel_snapshot(firm, company_type, scraped_date=scraped_date).jobs)


def fetch_citadel_snapshot(
    firm: str = "Citadel", company_type: str = "Prop Trading/Hedge Fund", *,
    scraped_date: datetime.date | None = None,
    diagnostics_dir: Path | None = None,
    headed: bool = False,
) -> CitadelSnapshot:
    """Use the same complete fetch for production and the credential-free probe."""
    return asyncio.run(_browser_snapshot(
        firm, company_type, scraped_date or datetime.date.today(), diagnostics_dir, headed,
    ))


async def _collect_snapshot(
    read_page: Callable[[int], Awaitable[ListingPage]],
) -> tuple[list[Listing], int]:
    jobs: list[Listing] = []
    seen: set[str] = set()
    total = None
    page_size = None
    page_number = 1
    while True:
        page = await read_page(page_number)
        if total is None:
            total = page.total
            page_size = len(page.jobs)
        elif page.total != total:
            raise IncompleteBoardResponse("citadel: pagination total changed during fetch")
        expected = min(page_size, total - len(jobs))
        if len(page.jobs) != expected or (total > 0 and not page.jobs):
            raise IncompleteBoardResponse("citadel: missing cards or unexpected page size")
        if len({job.job_id for job in page.jobs}) != len(page.jobs) or any(job.job_id in seen for job in page.jobs):
            raise IncompleteBoardResponse("citadel: repeated page or duplicate job identities")
        jobs.extend(page.jobs)
        seen.update(job.job_id for job in page.jobs)
        if page.shown != len(jobs):
            raise IncompleteBoardResponse("citadel: displayed count does not match fetched cards")
        if len(jobs) == total:
            if page.next_page is not None:
                raise IncompleteBoardResponse("citadel: pagination continues after reported total")
            return jobs, page_number
        if page.next_page != page_number + 1:
            raise IncompleteBoardResponse("citadel: missing or out-of-order next page")
        page_number += 1


def _to_job(raw: Listing, firm: str, company_type: str, date: datetime.date) -> Job:
    return Job(
        board=firm, job_id=raw.job_id, title=raw.title, department="",
        location=normalize_locations(raw.offices), url=raw.url, updated_at="",
        scraped_date=date, seniority=classify_seniority(raw.title, ""),
        company_type=company_type, source=PROVIDER_LABELS["citadel"],
    )


class _BrowserReader:
    def __init__(self, page, timeout_error, browser_error):
        self.page = page
        self.timeout_error = timeout_error
        self.browser_error = browser_error
        self.last_navigation: float | None = None
        self.cleared = False

    async def read(self, page_number: int) -> ListingPage:
        if self.last_navigation is not None:
            delay = NAVIGATION_INTERVAL_SECONDS - (time.monotonic() - self.last_navigation)
            if delay > 0:
                await asyncio.sleep(delay)
        self.last_navigation = time.monotonic()
        url = LISTING_URL if page_number == 1 else f"{LISTING_URL}page/{page_number}/"
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except self.browser_error as exc:
            raise CitadelNavigationError("citadel: browser navigation failed") from exc
        if response and response.status >= 500:
            raise CitadelNavigationError("citadel: listing server returned a transient error")
        if response and response.status == 429:
            raise CitadelChallengeError("citadel: listing request was rate-limited")
        try:
            await self.page.wait_for_function(_READY_SCRIPT, timeout=CHALLENGE_TIMEOUT_MS)
        except self.timeout_error as exc:
            title = (await self.page.title()).casefold()
            if any(marker in title for marker in ("just a moment", "attention required", "access denied")):
                raise CitadelChallengeError(
                    "citadel: Cloudflare challenge did not complete automatically"
                ) from exc
            # Inspect only visible text, never challenge scripts/tokens.
            body = (await self.page.locator("body").inner_text(timeout=2_000)).casefold()
            if "verify you are human" in body or "checking your browser" in body:
                raise CitadelChallengeError(
                    "citadel: Cloudflare challenge did not complete automatically"
                ) from exc
            raise IncompleteBoardResponse("citadel: listing content did not appear") from exc
        if urlsplit(self.page.url).path != urlsplit(url).path:
            raise IncompleteBoardResponse("citadel: listing redirected to an unexpected page")
        result = parse_listing_page(await self.page.content())
        if not self.cleared:
            self.cleared = True
            await self.page.context.route("**/*", _route_after_clearance)
        return result


async def _route_after_clearance(route):
    request = route.request
    host = urlsplit(request.url).hostname or ""
    # Keep all Cloudflare scripts, iframe resources and cookie-setting requests.
    if "/cdn-cgi/" in request.url or host == "challenges.cloudflare.com":
        await route.continue_()
    elif request.resource_type in {"image", "font", "media"} or host in {
        "www.google-analytics.com", "www.googletagmanager.com", "connect.facebook.net",
    }:
        await route.abort()
    else:
        await route.continue_()


async def _failure_screenshot(page, directory: Path | None) -> None:
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(directory / "failure.png"), timeout=2_000)
    except Exception:
        logger.warning("citadel: could not capture failure screenshot")


async def _browser_snapshot(firm, company_type, date, diagnostics_dir, headed):
    # Import only when Citadel is actually fetched. Unit tests need no browser.
    manager, PlaywrightTimeout, Error = _playwright()

    try:
        async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
            async with manager as playwright:
                try:
                    browser = await playwright.chromium.launch(channel="chromium", headless=not headed)
                except Error as exc:
                    raise CitadelBrowserError(
                        "citadel: Chromium could not start; install the matching Playwright browser"
                    ) from exc
                try:
                    context = await browser.new_context(
                        locale="en-SG", timezone_id="Asia/Singapore",
                        viewport={"width": 1440, "height": 900},
                    )
                    page = await context.new_page()
                    reader = _BrowserReader(page, PlaywrightTimeout, Error)
                    try:
                        for attempt in range(2):
                            try:
                                raw_jobs, pages = await _collect_snapshot(reader.read)
                                break
                            except (CitadelNavigationError, IncompleteBoardResponse):
                                if attempt:
                                    raise
                                logger.warning("citadel: incomplete fetch; retrying the whole snapshot once")
                        jobs = tuple(
                            _to_job(raw, firm, company_type, date) for raw in raw_jobs
                            if {"Singapore", "Hong Kong"}.intersection(raw.offices)
                        )
                        logger.info("citadel: complete snapshot, %d global and %d SG/HK jobs", len(raw_jobs), len(jobs))
                        return CitadelSnapshot(jobs, len(raw_jobs), pages)
                    except Exception:
                        await _failure_screenshot(page, diagnostics_dir)
                        raise
                finally:
                    await browser.close()
    except TimeoutError as exc:
        raise CitadelBrowserError("citadel: five-minute fetch deadline exceeded") from exc
    except Error as exc:
        raise CitadelBrowserError("citadel: browser session failed") from exc
