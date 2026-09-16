"""GET-with-retries shared by every job_boards/*/client.py.

Every board's client -- Greenhouse, Lever, Ashby, SmartRecruiters -- fetches
a public, read-only API over a plain HTTP GET and needs the exact same retry
policy: a transient failure (connection error, timeout, 5xx, 429) is worth
retrying since the next attempt might just work; anything else (a 4xx like a
bad/renamed board token) means the request itself is wrong, so retrying it
changes nothing and should raise immediately. Before this module existed,
each job_boards/*/client.py carried its own byte-for-byte copy of this loop
-- this is the one place it lives now, tested once (see
tests/job_boards/test_http.py) instead of four times.

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


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and (status >= 500 or status == 429)
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )
