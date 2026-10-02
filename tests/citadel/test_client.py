import asyncio
import dataclasses
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobscraper.enrichment.classify_region import classify_region
from jobscraper.job_boards._http import IncompleteBoardResponse
from jobscraper.job_boards.citadel import client
from jobscraper.job_boards.citadel.parser import LISTING_URL, parse_listing_page
from tests.citadel.test_parser import fixture


class BrowserError(Exception):
    pass


class BrowserTimeout(BrowserError):
    pass


def fake_page():
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        wait_for_function=AsyncMock(), content=AsyncMock(return_value=fixture()),
        title=AsyncMock(return_value="Open Opportunities"),
        screenshot=AsyncMock(), url=LISTING_URL,
        context=SimpleNamespace(route=AsyncMock()),
    )
    page.locator = lambda selector: SimpleNamespace(inner_text=AsyncMock(return_value=""))
    return page


def install_fake_browser(monkeypatch, page):
    browser = SimpleNamespace(
        new_context=AsyncMock(return_value=SimpleNamespace(new_page=AsyncMock(return_value=page))),
        close=AsyncMock(),
    )
    chromium = SimpleNamespace(launch=AsyncMock(return_value=browser))

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=chromium)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(client, "_playwright", lambda: (Manager(), BrowserTimeout, BrowserError))
    return browser, chromium


def test_complete_snapshot_filters_after_global_validation_and_maps_jobs(monkeypatch):
    first, last = [parse_listing_page(fixture(name)) for name in ("page-1.html", "page-2.html")]
    calls = []

    async def read(number):
        calls.append(number)
        return [first, last][number - 1]

    raw, pages = asyncio.run(client._collect_snapshot(read))
    assert calls == [1, 2] and pages == 2 and len(raw) == 3
    page = fake_page()
    browser, chromium = install_fake_browser(monkeypatch, page)

    async def collect(_read):
        return raw, pages

    monkeypatch.setattr(client, "_collect_snapshot", collect)
    date = datetime.date(2026, 10, 2)
    jobs = client.fetch_citadel_jobs("citadel", "Citadel", "Prop Trading/Hedge Fund", scraped_date=date)
    assert len(jobs) == 2
    assert jobs[0].location == "New York, NY | Greenwich, CT | Houston, TX | London, United Kingdom | Singapore"
    assert jobs[1].location == "Hong Kong | Singapore"
    assert all(classify_region(job.location) == "PAC" for job in jobs)
    assert jobs[1].seniority == "Intern"
    assert jobs[0].job_id == "software-engineer"
    assert all(job.source == "Citadel" and job.scraped_date == date for job in jobs)
    assert all(job.department == job.first_published == job.updated_at == job.description == "" for job in jobs)
    assert all(job.salary is None for job in jobs)
    chromium.launch.assert_awaited_once_with(channel="chromium", headless=True)
    browser.close.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"total": 4}, {"shown": 2}, {"jobs": ()},
    {"next_page": 3},
])
def test_invalid_final_page_cannot_return_partial_snapshot(change):
    first = parse_listing_page(fixture())
    last = dataclasses.replace(parse_listing_page(fixture("page-2.html")), **change)
    read = AsyncMock(side_effect=[first, last])
    with pytest.raises(IncompleteBoardResponse):
        asyncio.run(client._collect_snapshot(read))


def test_duplicate_global_ids_and_missing_next_are_rejected():
    first = parse_listing_page(fixture())
    last = parse_listing_page(fixture("page-2.html"))
    for pages in (
        [dataclasses.replace(first, next_page=None)],
        [first, dataclasses.replace(last, jobs=(first.jobs[0],))],
        [dataclasses.replace(first, jobs=(first.jobs[0], first.jobs[0]))],
    ):
        with pytest.raises(IncompleteBoardResponse):
            asyncio.run(client._collect_snapshot(AsyncMock(side_effect=pages)))


def test_total_fetch_deadline_and_browser_cleanup(monkeypatch):
    page = fake_page()
    browser, _chromium = install_fake_browser(monkeypatch, page)
    monkeypatch.setattr(client, "FETCH_TIMEOUT_SECONDS", 0.02)

    async def hang(_read):
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "_collect_snapshot", hang)
    with pytest.raises(client.CitadelBrowserError, match="deadline"):
        client.fetch_citadel_snapshot()
    browser.close.assert_awaited_once()


def test_challenge_is_not_retried_and_browser_closes_on_failure(monkeypatch, tmp_path):
    page = fake_page()
    browser, _chromium = install_fake_browser(monkeypatch, page)
    collect = AsyncMock(side_effect=client.CitadelChallengeError("Cloudflare challenge"))
    monkeypatch.setattr(client, "_collect_snapshot", collect)
    with pytest.raises(client.CitadelChallengeError):
        client.fetch_citadel_snapshot(diagnostics_dir=tmp_path)
    assert collect.await_count == 1
    page.screenshot.assert_awaited_once_with(path=str(tmp_path / "failure.png"), timeout=2_000)
    browser.close.assert_awaited_once()


@pytest.mark.parametrize("error", [client.CitadelNavigationError("navigation"), IncompleteBoardResponse("partial")])
def test_transient_failure_retries_entire_snapshot_once(monkeypatch, error):
    page = fake_page()
    browser, _chromium = install_fake_browser(monkeypatch, page)
    raw = list(parse_listing_page(fixture("page-2.html")).jobs)
    collect = AsyncMock(side_effect=[error, (raw, 1)])
    monkeypatch.setattr(client, "_collect_snapshot", collect)
    snapshot = client.fetch_citadel_snapshot()
    assert len(snapshot.jobs) == 1 and collect.await_count == 2
    browser.close.assert_awaited_once()


def test_browser_reader_allows_automatic_challenge_and_routes_after_clearance():
    page = fake_page()
    page.goto.return_value.status = 403
    reader = client._BrowserReader(page, BrowserTimeout, BrowserError)
    result = asyncio.run(reader.read(1))
    assert len(result.jobs) == 2
    page.context.route.assert_awaited_once()
    page.wait_for_function.assert_awaited_once_with(client._READY_SCRIPT, timeout=45_000)


def test_reader_challenge_timeout_does_not_return_empty_feed():
    page = fake_page()
    page.wait_for_function.side_effect = BrowserTimeout()
    page.title.return_value = "Just a moment..."
    with pytest.raises(client.CitadelChallengeError):
        asyncio.run(client._BrowserReader(page, BrowserTimeout, BrowserError).read(1))
    page.context.route.assert_not_awaited()


def test_browser_reader_preserves_navigation_spacing_across_retries(monkeypatch):
    page = fake_page()
    reader = client._BrowserReader(page, BrowserTimeout, BrowserError)
    monkeypatch.setattr(client.time, "monotonic", lambda: 100)
    sleep = AsyncMock()
    monkeypatch.setattr(client.asyncio, "sleep", sleep)

    async def run():
        await reader.read(1)
        await reader.read(1)

    asyncio.run(run())
    sleep.assert_awaited_once_with(10)
    page.context.route.assert_awaited_once()


@pytest.mark.parametrize("url,kind,blocked", [
    ("https://challenges.cloudflare.com/widget", "image", False),
    ("https://www.citadel.com/cdn-cgi/challenge-platform/test", "script", False),
    ("https://www.citadel.com/photo.png", "image", True),
    ("https://www.google-analytics.com/script", "script", True),
    (LISTING_URL, "document", False),
])
def test_routes_preserve_challenges_and_drop_heavy_resources(url, kind, blocked):
    route = SimpleNamespace(
        request=SimpleNamespace(url=url, resource_type=kind),
        abort=AsyncMock(), continue_=AsyncMock(),
    )
    asyncio.run(client._route_after_clearance(route))
    assert route.abort.await_count == int(blocked)
    assert route.continue_.await_count == int(not blocked)


def test_bad_token_cannot_start_a_browser():
    with pytest.raises(ValueError, match="token"):
        client.fetch_citadel_jobs("citadel-securities", "Citadel", "Prop Trading/Hedge Fund")
