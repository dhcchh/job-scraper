"""Shared retry and pagination helpers for job-board HTTP clients."""

import dataclasses
import logging
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter

from jobscraper.models import normalize_job_id

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
# Cap on an honored Retry-After, so one throttled board cannot stall a run.
MAX_RETRY_AFTER_SECONDS = 60.0

USER_AGENT = "latest-job-scraper/0.1"


def _new_session() -> requests.Session:
    """Share keep-alive connections across boards, pages and worker threads."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    return session


_session = _new_session()


class IncompleteBoardResponse(ValueError):
    """A successful HTTP response did not contain a complete jobs snapshot."""


def require_job_list(
    payload: object, source: str, identifier: str, *,
    key: str | None = None,
    id_key: str = "id",
    is_placeholder: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """Reject a changed or malformed API response instead of treating it as empty."""
    if key is not None:
        if not isinstance(payload, dict) or key not in payload:
            raise IncompleteBoardResponse(f"{source}[{identifier}]: missing {key!r}")
        payload = payload[key]
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise IncompleteBoardResponse(f"{source}[{identifier}]: invalid jobs list")
    _validate_ids(payload, id_key, is_placeholder)
    return payload


def _validate_ids(
    items: list[dict], id_key: str, is_placeholder: Callable[[dict], bool] | None = None
) -> None:
    """Missing or repeated identities make a snapshot unsafe for pruning.

    `is_placeholder` marks provider items that count toward the reported
    total but are not postings; they carry no identity to validate.
    """
    seen = set()
    for item in items:
        if is_placeholder is not None and is_placeholder(item):
            continue
        try:
            job_id = normalize_job_id(item.get(id_key))
        except ValueError as exc:
            raise IncompleteBoardResponse(f"missing or invalid {id_key}: {exc}") from exc
        if job_id in seen:
            raise IncompleteBoardResponse(f"duplicate {id_key}: {job_id}")
        seen.add(job_id)


def get_with_retries(
    source: str, identifier: str, url: str, *, params: dict | None = None
) -> requests.Response:
    """GET a URL using the shared transient-error retry policy."""
    return _send_with_retries(
        source, identifier, lambda: _session.get(url, params=params, timeout=30)
    )


def post_with_retries(
    source: str, identifier: str, url: str, *, json: dict
) -> requests.Response:
    """POST JSON using the shared transient-error retry policy."""
    return _send_with_retries(
        source, identifier, lambda: _session.post(url, json=json, timeout=30)
    )


def _send_with_retries(
    source: str, identifier: str, send: Callable[[], requests.Response]
) -> requests.Response:
    """Send a request with exponential backoff for transient failures."""
    attempt = 1
    while True:
        try:
            response = send()
            response.raise_for_status()
            return response
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
        ) as exc:
            if attempt >= MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            delay = _retry_after(exc) or BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "%s[%s]: attempt %d/%d failed (%s), retrying in %.0fs",
                source,
                identifier,
                attempt,
                MAX_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
            attempt += 1


@dataclasses.dataclass(frozen=True, slots=True)
class _PageFormat:
    """Where a provider's paginated response keeps its total, items and IDs."""

    total_key: str
    items_key: str
    id_key: str = "id"
    is_placeholder: Callable[[dict], bool] | None = None

    def total(self, payload: object) -> int:
        if not isinstance(payload, dict):
            raise IncompleteBoardResponse("pagination response is not an object")
        total = payload.get(self.total_key)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise IncompleteBoardResponse(
                f"missing or invalid {self.total_key!r} total"
            )
        return total

    def items(self, payload: object, cursor: int) -> list[dict]:
        return require_job_list(
            payload, "pagination", str(cursor), key=self.items_key,
            id_key=self.id_key, is_placeholder=self.is_placeholder,
        )

    def validate_ids(self, items: list[dict]) -> None:
        _validate_ids(items, self.id_key, self.is_placeholder)


def _fetch_twice(
    fetch: Callable[[], list[dict]], source: str, identifier: str, fetch_name: str
) -> list[dict]:
    """Fetch a whole board, and once more if its snapshot is incomplete."""
    try:
        return fetch()
    except IncompleteBoardResponse as exc:
        logger.warning(
            "%s[%s]: incomplete %s (%s), retrying the whole board once",
            source,
            identifier,
            fetch_name,
            exc,
        )
    return fetch()


def paginate_with_retries(
    source: str,
    identifier: str,
    url: str,
    *,
    cursor_param: str,
    page_size_param: str,
    page_size: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    is_placeholder: Callable[[dict], bool] | None = None,
    method: str = "GET",
    body: dict | None = None,
    workers: int = 1,
) -> list[dict]:
    """Fetch every page, optionally in parallel after the first.

    Placeholders are returned with the postings, so page counts still match
    the reported total; the caller drops them.
    """
    pages = _PageFormat(total_key, items_key, id_key, is_placeholder)

    def fetch_page(cursor: int) -> dict:
        page_args = {cursor_param: cursor, page_size_param: page_size}
        if method == "POST":
            response = post_with_retries(
                source, identifier, url, json={**(body or {}), **page_args}
            )
        else:
            response = get_with_retries(source, identifier, url, params=page_args)
        return response.json()

    if workers > 1:
        try:
            items = _paginate_in_parallel(fetch_page, pages, workers)
        except Exception as exc:
            logger.warning(
                "%s[%s]: parallel page fetch failed (%s), refetching one page "
                "at a time",
                source,
                identifier,
                exc,
            )
        else:
            if items is not None:
                return items
            logger.warning(
                "%s[%s]: parallel page fetch came back with a different "
                "number of items than the first page's total, refetching one "
                "page at a time",
                source,
                identifier,
            )
    return _fetch_twice(
        lambda: _paginate_sequentially(fetch_page, pages),
        source,
        identifier,
        "sequential page fetch",
    )


def _paginate_sequentially(
    fetch_page: Callable[[int], dict], pages: _PageFormat
) -> list[dict]:
    """Fetch pages sequentially until the endpoint is exhausted."""
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        payload = fetch_page(cursor)
        page_total = pages.total(payload)
        if total is None:
            total = page_total
        elif page_total not in (0, total):
            raise IncompleteBoardResponse("pagination total changed during fetch")
        page = pages.items(payload, cursor)
        if not page:
            if cursor < total:
                raise IncompleteBoardResponse(
                    f"empty page at offset {cursor} before total {total}"
                )
            break
        items.extend(page)
        cursor += len(page)
        if cursor > total:
            raise IncompleteBoardResponse(
                f"received {cursor} items, more than reported total {total}"
            )
    pages.validate_ids(items)
    return items


def _paginate_in_parallel(
    fetch_page: Callable[[int], dict], pages: _PageFormat, workers: int
) -> list[dict] | None:
    """Fetch remaining pages concurrently when the total is known."""
    first = fetch_page(0)
    total = pages.total(first)
    items = list(pages.items(first, 0))
    if len(items) == total:
        return items
    if not items or len(items) > total:
        return None
    stride = len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(fetch_page, range(stride, total, stride)))
    for payload in payloads:
        if pages.total(payload) not in (0, total):
            return None
        items.extend(pages.items(payload, len(items)))
    pages.validate_ids(items)
    return items if len(items) == total else None


def paginate_with_overlap(
    source: str,
    identifier: str,
    url: str,
    *,
    cursor_param: str,
    page_size_param: str,
    page_size: int,
    overlap: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    check_first_page: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Fetch every page of a listing that may change while it is paged.

    Pages are fetched one at a time, each starting `overlap` items before
    the previous one ended. A posting added or removed ahead of the cursor
    shifts every later page, so each page continues after the previous
    page's last item, found again in the overlap, instead of at a fixed
    offset: no posting listed throughout the fetch is skipped or repeated.
    If that item isn't on the page (a shift toward the start of `overlap`
    items or more), or an item repeats, the board is fetched once more and
    then fails.

    `check_first_page` sees the first response before any other page is
    requested, to reject a listing that can't be fetched completely.
    """
    if not 0 < overlap < page_size:
        raise ValueError("overlap must be positive and smaller than a page")
    pages = _PageFormat(total_key, items_key, id_key)

    def fetch_page(cursor: int) -> dict:
        response = get_with_retries(
            source, identifier, url,
            params={cursor_param: cursor, page_size_param: page_size},
        )
        return response.json()

    return _fetch_twice(
        lambda: _paginate_overlapping(
            fetch_page, pages, page_size, overlap, check_first_page
        ),
        source,
        identifier,
        "page fetch",
    )


def _paginate_overlapping(
    fetch_page: Callable[[int], dict], pages: _PageFormat, page_size: int,
    overlap: int, check_first_page: Callable[[dict], None] | None,
) -> list[dict]:
    items: list[dict] = []
    cursor = 0
    while True:
        payload = fetch_page(cursor)
        total = pages.total(payload)
        if cursor == 0 and check_first_page is not None:
            check_first_page(payload)
        page = pages.items(payload, cursor)
        end = cursor + len(page)
        if end > total:
            raise IncompleteBoardResponse(
                f"page at offset {cursor} ends at {end}, past its total {total}"
            )
        if items:
            anchor = normalize_job_id(items[-1][pages.id_key])
            page_ids = [normalize_job_id(item[pages.id_key]) for item in page]
            if anchor not in page_ids:
                raise IncompleteBoardResponse(
                    f"listing shifted by {overlap} or more items before "
                    f"offset {cursor}"
                )
            items.extend(page[page_ids.index(anchor) + 1:])
        else:
            items.extend(page)
        if end == total:
            break
        if len(page) < page_size:
            raise IncompleteBoardResponse(
                f"short page at offset {cursor}: {len(page)} item(s), "
                f"total {total}"
            )
        cursor = end - overlap
    pages.validate_ids(items)
    return items


def paginate_numbered_pages(
    source: str,
    identifier: str,
    fetch_page: Callable[[int], dict],
    *,
    page_size: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    workers: int = 1,
    repair_rounds: int = 3,
    check_first_page: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Fetch every page of a newest-first listing paged by number from 1.

    This is for an endpoint with a fixed page size and no offset, so pages
    can't overlap. Such a listing can order tied items differently on each
    request. A tied item on one side of a page boundary can then appear on
    both pages while its neighbour appears on neither, and the page lengths
    still add up to the total. Pages holding a repeated ID are fetched
    again and their items merged, up to `repair_rounds` times, until the
    distinct IDs reach the total.

    Every page must report the first page's total. Once every item is seen,
    the first page is fetched once more: an item on it that wasn't seen was
    added during the fetch. Either change, or repairs that don't reach the
    total, fetch the board once more and then fail it.

    `check_first_page` sees the first response before any other page is
    requested.
    """
    pages = _PageFormat(total_key, items_key, id_key)
    return _fetch_twice(
        lambda: _paginate_numbered(
            fetch_page, pages, page_size, workers, repair_rounds, check_first_page,
            f"{source}[{identifier}]",
        ),
        source,
        identifier,
        "page fetch",
    )


def _paginate_numbered(
    fetch_page: Callable[[int], dict], pages: _PageFormat, page_size: int,
    workers: int, repair_rounds: int,
    check_first_page: Callable[[dict], None] | None, label: str,
) -> list[dict]:
    first = fetch_page(1)
    total = pages.total(first)
    if check_first_page is not None:
        check_first_page(first)
    page_count = max(1, math.ceil(total / page_size))

    def read(number: int, payload: dict) -> list[dict]:
        if pages.total(payload) != total:
            raise IncompleteBoardResponse("pagination total changed during fetch")
        items = pages.items(payload, number)
        expected = min(page_size, total - (number - 1) * page_size)
        if len(items) != expected:
            raise IncompleteBoardResponse(
                f"page {number} has {len(items)} item(s), expected {expected} "
                f"of total {total}"
            )
        return items

    def fetch(numbers: list[int]) -> dict[int, list[dict]]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            payloads = list(pool.map(fetch_page, numbers))
        return {number: read(number, payload) for number, payload in zip(numbers, payloads)}

    def item_id(item: dict) -> str:
        return normalize_job_id(item[pages.id_key])

    page_items = {1: read(1, first), **fetch(list(range(2, page_count + 1)))}
    seen: dict[str, dict] = {}
    for _round in range(repair_rounds + 1):
        for items in page_items.values():
            for item in items:
                seen.setdefault(item_id(item), item)
        if len(seen) >= total or _round == repair_rounds:
            break
        repeated = _pages_with_repeated_ids(page_items, item_id)
        logger.info(
            "%s: %d of %d items seen, refetching page(s) %s that repeat an item",
            label, len(seen), total, ", ".join(map(str, repeated)),
        )
        page_items.update(fetch(repeated))
    if len(seen) != total:
        raise IncompleteBoardResponse(
            f"{len(seen)} distinct item(s) after {repair_rounds} repair "
            f"round(s), total {total}"
        )
    if page_count > 1 and any(
        item_id(item) not in seen for item in read(1, fetch_page(1))
    ):
        raise IncompleteBoardResponse("an item was added during the fetch")
    return list(seen.values())


def _pages_with_repeated_ids(
    page_items: dict[int, list[dict]], item_id: Callable[[dict], str]
) -> list[int]:
    """Return the numbers of the pages holding an item another page also holds."""
    pages_by_id: dict[str, set[int]] = {}
    for number, items in page_items.items():
        for item in items:
            pages_by_id.setdefault(item_id(item), set()).add(number)
    return sorted({
        number
        for numbers in pages_by_id.values() if len(numbers) > 1
        for number in numbers
    })


def _retry_after(exc: Exception) -> float | None:
    """Read a 429 response's Retry-After seconds, capped; ignore HTTP dates."""
    response = getattr(exc, "response", None)
    if response is None or response.status_code != 429:
        return None
    try:
        seconds = float((getattr(response, "headers", None) or {}).get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )
