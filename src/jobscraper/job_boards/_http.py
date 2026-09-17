"""GET-with-retries shared by every job_boards/*/client.py.

Every board's client -- Greenhouse, Lever, Ashby, SmartRecruiters, and
Eightfold -- fetches a public, read-only API over a plain HTTP GET and needs
the exact same retry policy: a transient failure (connection error, timeout,
5xx, 429) is worth retrying since the next attempt might just work; anything
else (a 4xx like a bad/renamed board token) means the request itself is
wrong, so retrying it changes nothing and should raise immediately. Before
this module existed, each of the first four job_boards/*/client.py modules
carried its own byte-for-byte copy of this loop (Eightfold's client came
later and was written directly against this module) -- this is the one
place it lives now, tested once (see tests/job_boards/test_http.py) instead
of once per board client.

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

    Retries a connection error/timeout (no response at all) or an HTTPError
    whose response is a 5xx or 429 (server trouble/rate-limited -- worth
    trying again) up to MAX_ATTEMPTS times total. Any other HTTPError (a 4xx
    like a bad/renamed board token) raises immediately: the request itself
    is wrong, so retrying it changes nothing.
    """
    attempt = 1
    while True:
        try:
            response = requests.get(url, params=params, timeout=30)
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
) -> list[dict]:
    """Every item across every page of a paginated JSON GET endpoint.

    Shared by smartrecruiters/client.py and eightfold/client.py -- the two
    paginated board types -- instead of each keeping its own copy of this
    loop; before this existed the two were near-identical, differing only in
    field/param names.

    Advances `cursor_param` (e.g. "offset"/"start") by however many items
    the page actually returned -- not by `page_size`, since a server may cap
    or otherwise not honor the requested `page_size_param` (SmartRecruiters
    caps `limit` at 100; Eightfold ignores `num` entirely and always returns
    10) -- and stops once the response's own `total_key` count has been
    collected or a page comes back empty under `items_key` (belt-and-braces
    against an off-by-one in that count looping forever).
    """
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        response = get_with_retries(
            source,
            identifier,
            url,
            params={cursor_param: cursor, page_size_param: page_size},
        )
        payload = response.json()
        total = payload.get(total_key, 0)
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
