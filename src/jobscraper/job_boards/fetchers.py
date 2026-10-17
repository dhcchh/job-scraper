"""Public fetch functions for every supported board provider."""

import dataclasses
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from jobscraper import config
from jobscraper.job_boards.ashby.client import fetch_ashby_jobs
from jobscraper.job_boards.citadel.client import fetch_citadel_jobs
from jobscraper.job_boards.eightfold.client import fetch_eightfold_jobs
from jobscraper.job_boards.greenhouse.client import fetch_greenhouse_jobs
from jobscraper.job_boards.lever.client import fetch_lever_jobs
from jobscraper.job_boards.recruitee.client import fetch_recruitee_jobs
from jobscraper.job_boards.smartrecruiters.client import fetch_smartrecruiters_jobs
from jobscraper.job_boards.workday.client import fetch_workday_jobs
from jobscraper.models import Job

logger = logging.getLogger(__name__)

Fetcher = Callable[..., list[Job]]

BOARD_WORKERS = 8
# Each Workday board already runs its own page and detail workers, so fewer
# Workday boards run at once, in a pool of their own.
WORKDAY_BOARD_WORKERS = 4


def fetcher_for(source: str) -> Fetcher:
    """Return a provider's fetch function, looked up at call time."""
    return globals()[f"fetch_{source}_jobs"]


def board_sources() -> list[tuple[str, list[dict], Fetcher]]:
    """Return each configured provider and its fetch function."""
    return [
        (name, boards, fetcher_for(name))
        for name, _label, boards in config.board_sources()
    ]


def fetch_board(source: str, board: dict, fetch: Fetcher) -> list[Job]:
    """Apply optional provider settings consistently in every command."""
    kwargs = (
        {"location_scope": board["location_scope"]}
        if source == "workday" and board.get("location_scope")
        else {}
    )
    return fetch(board["token"], board["firm"], board["company_type"], **kwargs)


@dataclasses.dataclass(frozen=True, slots=True)
class BoardFetch:
    """One board's jobs, or the exception that failed it."""

    source: str
    board: dict
    jobs: list[Job]
    error: Exception | None = None


def fetch_boards(
    sources: list[tuple[str, list[dict], Fetcher]],
    *,
    retry_failed_after: float | None = None,
) -> list[BoardFetch]:
    """Fetch every board concurrently; results keep configuration order.

    With `retry_failed_after`, each failed board is fetched once more that
    many seconds after the first pass. A provider glitch such as an
    inconsistent page can clear within minutes, and a later complete
    snapshot is as valid as the first.
    """
    tasks = [(source, board, fetch) for source, boards, fetch in sources for board in boards]
    results = _fetch_all(tasks)
    failed = [index for index, result in enumerate(results) if result.error is not None]
    if retry_failed_after is None or not failed:
        return results

    for index in failed:
        result = results[index]
        logger.warning(
            "%s[%s]: failed (%s), retrying once in %.0fs",
            result.source,
            result.board["firm"],
            describe_error(result.error),
            retry_failed_after,
        )
    time.sleep(retry_failed_after)
    for index, retried in zip(failed, _fetch_all([tasks[index] for index in failed])):
        if retried.error is None:
            logger.info("%s[%s]: succeeded on retry", retried.source, retried.board["firm"])
        results[index] = retried
    return results


def describe_error(error: Exception, limit: int = 300) -> str:
    """One-line exception type and message for logs and run summaries."""
    text = " ".join(f"{type(error).__name__}: {error}".split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fetch_all(tasks: list[tuple[str, dict, Fetcher]]) -> list[BoardFetch]:
    with (
        ThreadPoolExecutor(BOARD_WORKERS, thread_name_prefix="board") as shared,
        ThreadPoolExecutor(WORKDAY_BOARD_WORKERS, thread_name_prefix="workday") as workday,
    ):
        futures = [
            (workday if source == "workday" else shared).submit(
                _fetch_one, source, board, fetch
            )
            for source, board, fetch in tasks
        ]
        return [future.result() for future in futures]


def _fetch_one(source: str, board: dict, fetch: Fetcher) -> BoardFetch:
    try:
        return BoardFetch(source, board, fetch_board(source, board, fetch))
    except Exception as exc:
        return BoardFetch(source, board, [], exc)
