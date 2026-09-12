"""Entrypoint: fetch every configured board's jobs, classify + route each into
one of jobscraper.classifications.classify_roles's role categories, append
new postings per category, prune closed ones.

    uv run python -m jobscraper.main

Loops over GREENHOUSE_BOARD_TOKENS from config, one board at a time -- no
concurrency, since this runs once a day and there's no rate-limit pressure
at this scale. Every fetched job is then classified by
classify_roles.classify_job into a category, each of which is its own tab in
the spreadsheet; a job matching no category (HR, Legal, Sales, ...) is
dropped rather than written anywhere. Each job's seniority level (Intern,
Senior, Manager, ... or "Unspecified") is computed earlier, at fetch time --
see classify_seniority.classify_seniority and job_boards/greenhouse/client.py
-- so it's already set on every Job here and just rides along as a column.

TODO(extension points), roughly in the order you'll likely reach for them:
  - more boards: add e.g. jobscraper/job_boards/lever/client.py with its own
    fetch_jobs() -> list[Job], and extend all_jobs below the same way.
  - more/edited categories: see classifications/classify_roles.py's
    CATEGORIES -- add a keyword there, or a whole new category + matching
    spreadsheet tab. Same idea for seniority levels, in classify_seniority.py.
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
from jobscraper.classifications.classify_roles import CATEGORIES, classify_job
from jobscraper.job_boards.greenhouse.client import fetch_jobs
from jobscraper.models import Job
from jobscraper.sheets.writer import (
    append_jobs,
    get_spreadsheet,
    get_worksheet,
    prune_closed_postings,
)

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

    jobs_by_category: dict[str, list[Job]] = {name: [] for name, _ in CATEGORIES}
    noise_count = 0
    for job in all_jobs:
        category = classify_job(job)
        if category is None:
            noise_count += 1
        else:
            jobs_by_category[category].append(job)

    logger.info(
        "classified %d job(s) across %d categor(y/ies), filtered %d as noise",
        len(all_jobs) - noise_count,
        len(CATEGORIES),
        noise_count,
    )

    spreadsheet = get_spreadsheet()
    fetched_boards = set(config.GREENHOUSE_BOARD_TOKENS)
    for category, _keywords in CATEGORIES:
        category_jobs = jobs_by_category[category]
        worksheet = get_worksheet(spreadsheet, category)
        append_jobs(worksheet, category_jobs)
        prune_closed_postings(worksheet, category_jobs, fetched_boards=fetched_boards)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()
