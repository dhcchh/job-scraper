"""Entrypoint: fetch every configured board's jobs, append new ones, prune closed ones.

    uv run python -m jobscraper.main

Loops over GREENHOUSE_BOARD_TOKENS from config, one board at a time -- no
concurrency, since this runs once a day and there's no rate-limit pressure
at this scale.

TODO(extension points), roughly in the order you'll likely reach for them:
  - more boards: add e.g. jobscraper/lever/client.py with its own
    fetch_jobs() -> list[Job], and extend all_jobs below the same way.
  - filtering: filter all_jobs by title/department keyword before
    appending.
  - retries/backoff: greenhouse.client.fetch_jobs raises immediately on any
    HTTP error, and the loop below doesn't catch per-board either -- one
    bad board or one Greenhouse hiccup currently aborts the whole run. Note
    that prune_closed_postings below assumes every configured board
    succeeded this run (see its docstring) -- fix that assumption at the
    same time as adding per-board error handling.
  - notifications: report success/failure/job counts somewhere (Slack,
    email, a GitHub Actions job summary) instead of only the run log.
"""

import logging

from jobscraper import config
from jobscraper.greenhouse.client import fetch_jobs
from jobscraper.models import Job
from jobscraper.sheets.writer import append_jobs, get_worksheet, prune_closed_postings

logger = logging.getLogger(__name__)


def run() -> None:
    all_jobs: list[Job] = []
    for board_token in config.GREENHOUSE_BOARD_TOKENS:
        jobs = fetch_jobs(board_token)
        all_jobs.extend(jobs)

    logger.info(
        "fetched %d jobs total across %d board(s)",
        len(all_jobs),
        len(config.GREENHOUSE_BOARD_TOKENS),
    )

    worksheet = get_worksheet()
    append_jobs(worksheet, all_jobs)
    prune_closed_postings(
        worksheet, all_jobs, fetched_boards=set(config.GREENHOUSE_BOARD_TOKENS)
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()
