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
) -> list[dict]:
    """Every item across every page of a paginated JSON endpoint.

    Shared by smartrecruiters/client.py, eightfold/client.py, and
    workday/client.py -- the three paginated board types -- instead of each
    keeping its own copy of this loop; before this existed the first two were
    near-identical, differing only in field/param names.

    For `method="GET"` the cursor and page size are query params. For
    `method="POST"` (Workday) they're merged into the JSON `body` instead.

    Advances `cursor_param` (e.g. "offset"/"start") by however many items
    the page actually returned -- not by `page_size`, since a server may cap
    or otherwise not honor the requested `page_size_param` (SmartRecruiters
    caps `limit` at 100; Eightfold ignores `num` entirely and always returns
    10) -- and stops once the response's own `total_key` count has been
    collected or a page comes back empty under `items_key` (belt-and-braces
    against an off-by-one in that count looping forever). A later page
    reporting a zero/missing total keeps the earlier one: Workday only fills
    in `total` on the first page and sends 0 on every page after it
    (confirmed on Visa's and Mastercard's live boards), which would otherwise
    stop the loop after page two.
    """
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        page_args = {cursor_param: cursor, page_size_param: page_size}
        if method == "POST":
            response = post_with_retries(
                source, identifier, url, json={**(body or {}), **page_args}
            )
        else:
            response = get_with_retries(source, identifier, url, params=page_args)
        payload = response.json()
        total = payload.get(total_key) or total or 0
        page = payload.get(items_key, [])
        if not page:
            break
        items.extend(page)
        cursor += len(page)
    return items


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )
