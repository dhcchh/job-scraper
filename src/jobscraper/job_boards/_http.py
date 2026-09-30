"""Shared retry and pagination helpers for job-board HTTP clients."""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import requests

from jobscraper.models import normalize_job_id

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


class IncompleteBoardResponse(ValueError):
    """A successful HTTP response did not contain a complete jobs snapshot."""


def require_job_list(
    payload: object, source: str, identifier: str, *,
    key: str | None = None,
    id_key: str = "id",
) -> list[dict]:
    """Reject a changed or malformed API response instead of treating it as empty."""
    if key is not None:
        if not isinstance(payload, dict) or key not in payload:
            raise IncompleteBoardResponse(f"{source}[{identifier}]: missing {key!r}")
        payload = payload[key]
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise IncompleteBoardResponse(f"{source}[{identifier}]: invalid jobs list")
    _validate_ids(payload, id_key)
    return payload


def _validate_ids(items: list[dict], id_key: str) -> None:
    """Missing or repeated identities make a snapshot unsafe for pruning."""
    seen = set()
    for item in items:
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
    id_key: str = "id",
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
            items = _paginate_in_parallel(fetch_page, total_key, items_key, workers, id_key)
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
    for attempt in range(2):
        try:
            return _paginate_sequentially(fetch_page, total_key, items_key, id_key)
        except IncompleteBoardResponse:
            if attempt:
                raise
            logger.warning(
                "%s[%s]: incomplete sequential page fetch, retrying the "
                "whole board once",
                source,
                identifier,
            )
    raise AssertionError("unreachable")


def _page_total(payload: object, total_key: str) -> int:
    if not isinstance(payload, dict):
        raise IncompleteBoardResponse("pagination response is not an object")
    total = payload.get(total_key)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise IncompleteBoardResponse(f"missing or invalid {total_key!r} total")
    return total


def _paginate_sequentially(
    fetch_page: Callable[[int], dict], total_key: str, items_key: str, id_key: str
) -> list[dict]:
    """Fetch pages sequentially until the endpoint is exhausted."""
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        payload = fetch_page(cursor)
        page_total = _page_total(payload, total_key)
        if total is None:
            total = page_total
        elif page_total not in (0, total):
            raise IncompleteBoardResponse("pagination total changed during fetch")
        page = require_job_list(
            payload, "pagination", str(cursor), key=items_key, id_key=id_key
        )
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
    _validate_ids(items, id_key)
    return items


def _paginate_in_parallel(
    fetch_page: Callable[[int], dict], total_key: str, items_key: str,
    workers: int, id_key: str,
) -> list[dict] | None:
    """Fetch remaining pages concurrently when the total is known."""
    first = fetch_page(0)
    total = _page_total(first, total_key)
    items = list(require_job_list(first, "pagination", "0", key=items_key, id_key=id_key))
    if len(items) == total:
        return items
    if not items or len(items) > total:
        return None
    stride = len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(fetch_page, range(stride, total, stride)))
    for payload in payloads:
        page_total = _page_total(payload, total_key)
        if page_total not in (0, total):
            return None
        items.extend(require_job_list(
            payload, "pagination", str(len(items)), key=items_key, id_key=id_key
        ))
    _validate_ids(items, id_key)
    return items if len(items) == total else None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )
