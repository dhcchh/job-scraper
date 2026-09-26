"""GET/POST-with-retries shared by every job_boards/*/client.py.

Every board's client -- Greenhouse, Lever, Ashby, SmartRecruiters,
Eightfold, and Workday -- fetches a public, read-only API (a plain HTTP GET,
or for Workday's search endpoint a read-only JSON POST) and needs the exact
same retry policy: a transient failure (connection error, timeout,
5xx, 429) is worth retrying since the next attempt might just work; anything
else (a 4xx like a bad/renamed board token) means the request itself is
wrong, so retrying it changes nothing and should raise immediately. Before
this module existed, each of the first four job_boards/*/client.py modules
carried its own byte-for-byte copy of this loop (Eightfold's and Workday's
clients came later and were written directly against this module) -- this
is the one place it lives now, tested once (see tests/job_boards/test_http.py)
instead of once per board client.

3 attempts total (1 initial + 2 retries), 2s/4s backoff between them --
enough to ride out a brief network blip or a board's hiccup without
stalling a daily run for long if it's not going to recover. Exhausting all
retries still raises -- main.py (and sheets/backfill.py's _fetch_overrides)
are what decide a failed board shouldn't abort the whole run, not this
module; they catch per-board and skip.
"""

from __future__ import annotations

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
    """GET url, retrying transient failures with exponential backoff.

    `source` (e.g. "greenhouse") and `identifier` (that board's own
    token/slug/company-identifier) only label the retry-warning log line --
    e.g. "greenhouse[wehrtyou]: attempt 1/3 failed (...), retrying in 2s" --
    matching what each client's own retry loop logged before this was
    extracted. `params` defaults to None (no query string), for Ashby's
    client, which doesn't need one.

    See _send_with_retries for which failures are retried.
    """
    return _send_with_retries(
        source, identifier, lambda: requests.get(url, params=params, timeout=30)
    )


def post_with_retries(
    source: str, identifier: str, url: str, *, json: dict
) -> requests.Response:
    """POST a JSON body to url, with the same retry policy as get_with_retries.

    Only Workday needs this: its job-search endpoint is a POST even though it
    only reads (see job_boards/workday/client.py).
    """
    return _send_with_retries(
        source, identifier, lambda: requests.post(url, json=json, timeout=30)
    )


def _send_with_retries(
    source: str, identifier: str, send: Callable[[], requests.Response]
) -> requests.Response:
    """Call send() until it returns a non-error response, with backoff.

    Retries a connection error/timeout (no response at all) or an HTTPError
    whose response is a 5xx or 429 (server trouble/rate-limited -- worth
    trying again) up to MAX_ATTEMPTS times total. Any other HTTPError (a 4xx
    like a bad/renamed board token) raises immediately: the request itself
    is wrong, so retrying it changes nothing.
    """
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
    """Every item across every page of a paginated JSON endpoint.

    Shared by smartrecruiters/client.py, eightfold/client.py, and
    workday/client.py -- the three paginated board types -- instead of each
    keeping its own copy of this loop; before this existed the first two were
    near-identical, differing only in field/param names.

    For `method="GET"` the cursor and page size are query params. For
    `method="POST"` (Workday) they're merged into the JSON `body` instead.

    `workers=1` (the default) fetches one page after another -- see
    _paginate_sequentially. `workers > 1` fetches every page after the first
    `workers` at a time instead -- see _paginate_in_parallel -- and falls
    back to the one-page-at-a-time fetch, from scratch, if that fails or
    comes back incomplete, so a board that starts rate-limiting parallel
    requests (or a listing that shifts mid-fetch) costs time, not postings.
    """

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
    """One page after another.

    Advances the cursor by however many items the page actually returned --
    not by the requested page size, since a server may cap or otherwise not
    honor it (SmartRecruiters caps `limit` at 100; Eightfold ignores `num`
    entirely and always returns 10) -- and stops once the response's own
    `total_key` count has been collected or a page comes back empty under
    `items_key` (belt-and-braces against an off-by-one in that count looping
    forever). A later page reporting a zero/missing total keeps the earlier
    one: Workday only fills in `total` on the first page and sends 0 on
    every page after it (confirmed on Visa's and Mastercard's live boards),
    which would otherwise stop the loop after page two.
    """
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
    """Every page, the first one alone and the rest `workers` at a time, or
    None if the result doesn't add up to the first page's total.

    The first page is what says how many items there are (`total_key`) and
    how many the server actually puts on a page (its length), which is all
    it takes to know every other page's cursor up front. That's worth it
    where each request is slow server-side: Workday takes ~1.3s per page
    whether or not the connection is reused, so Mastercard's 53 pages take
    ~80-115s one at a time and ~13s eight at a time (both measured).

    Returns None -- for the caller to fall back to _paginate_sequentially --
    when the pages don't add up to exactly that total, e.g. a page came
    back short because postings closed mid-fetch. A failed page (after
    get/post_with_retries' own retries) raises, same as the sequential
    fetch. Pages are returned in cursor order either way.
    """
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
