"""Shared session, retries and response validation for job-board clients."""

import logging
import math
import time
from collections.abc import Callable

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
) -> list[dict]:
    """Reject a changed or malformed API response instead of treating it as empty."""
    if key is not None:
        if not isinstance(payload, dict) or key not in payload:
            raise IncompleteBoardResponse(f"{source}[{identifier}]: missing {key!r}")
        payload = payload[key]
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise IncompleteBoardResponse(f"{source}[{identifier}]: invalid jobs list")
    validate_ids(payload, id_key)
    return payload


def validate_ids(items: list[dict], id_key: str) -> None:
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
        source, identifier, lambda: _session.get(url, params=params, timeout=30)
    )


def post_with_retries(
    source: str, identifier: str, url: str, *,
    json: dict,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """POST JSON using the shared transient-error retry policy.

    `headers` are added to the session's for this request only.
    """
    extra = {"headers": headers} if headers else {}
    return _send_with_retries(
        source, identifier,
        lambda: _session.post(url, json=json, timeout=30, **extra),
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
