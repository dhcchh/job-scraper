"""Shared retry and pagination helpers for job-board HTTP clients."""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


def get_with_retries(
    source: str, identifier: str, url: str, *, params: dict | None = None
) -> requests.Response:
    """GET a URL using the shared transient-error retry policy."""
    return _send_with_retries(
        source, identifier, lambda: requests.get(url, params=params, timeout=30)
    )


def post_with_retries(
    source: str, identifier: str, url: str, *, json: dict
) -> requests.Response:
    """POST JSON using the shared transient-error retry policy."""
    return _send_with_retries(
        source, identifier, lambda: requests.post(url, json=json, timeout=30)
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
            delay = BACKOFF_SECONDS * (2 ** (attempt - 1))
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
    method: str = "GET",
    body: dict | None = None,
    workers: int = 1,
) -> list[dict]:
    """Fetch every page, optionally in parallel after the first."""

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
            items = _paginate_in_parallel(fetch_page, total_key, items_key, workers)
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
    return _paginate_sequentially(fetch_page, total_key, items_key)


def _paginate_sequentially(
    fetch_page: Callable[[int], dict], total_key: str, items_key: str
) -> list[dict]:
    """Fetch pages sequentially until the endpoint is exhausted."""
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        payload = fetch_page(cursor)
        total = payload.get(total_key) or total or 0
        page = payload.get(items_key, [])
        if not page:
            break
        items.extend(page)
        cursor += len(page)
    return items


def _paginate_in_parallel(
    fetch_page: Callable[[int], dict], total_key: str, items_key: str, workers: int
) -> list[dict] | None:
    """Fetch remaining pages concurrently when the total is known."""
    first = fetch_page(0)
    total = first.get(total_key) or 0
    items = list(first.get(items_key, []))
    if not items or len(items) >= total:
        return items
    stride = len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(fetch_page, range(stride, total, stride)))
    for payload in payloads:
        items.extend(payload.get(items_key, []))
    return items if len(items) == total else None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )
