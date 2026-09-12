"""Entrypoint: fetch every configured board's jobs, classify + route each into
one of jobscraper.classifications.classify_roles's role categories, append
new postings per category, prune closed ones.

    uv run python -m jobscraper.main

Loops over GREENHOUSE_BOARDS from config, one board at a time -- no
concurrency, since this runs once a day and there's no rate-limit pressure
at this scale. A board whose fetch fails (greenhouse.client.fetch_jobs
already retries transient errors -- see that module -- so what reaches here
has exhausted those) is logged and skipped rather than aborting the whole
run; its firm is left out of fetched_boards, so prune_closed_postings never
mistakes "we couldn't check this board" for "everything on it just closed"
(see that function's docstring). Every job from a board that DID succeed is
then classified by classify_roles.classify_job into a category, each of
which is its own tab in the spreadsheet; a job matching no category (HR,
Legal, Sales, ...) is dropped rather than written anywhere. Each job's
seniority level (Intern, Senior, Manager, ... or "Unspecified") is computed
earlier, at fetch time -- see classify_seniority.classify_seniority and
job_boards/greenhouse/client.py -- so it's already set on every Job here and
just rides along as a column. Each category's tab is kept sorted (most
recent first, then location, then company_type, then firm) by
sheets.writer.sort_rows after every append/prune.

TODO(extension points), roughly in the order you'll likely reach for them:
  - more boards: add e.g. jobscraper/job_boards/lever/client.py with its own
    fetch_jobs() -> list[Job], and extend the fetch loop below the same way.
  - more/edited categories: see classifications/classify_roles.py's
    CATEGORIES -- add a keyword there, or a whole new category + matching
    spreadsheet tab. Same idea for seniority levels, in classify_seniority.py.
  - notifications: report success/failure/job counts somewhere (Slack,
    email, a GitHub Actions job summary) instead of only the run log --
    failed_firms below already has the list, just not sent anywhere yet.
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
    sort_rows,
)

logger = logging.getLogger(__name__)


def run() -> None:
    all_jobs: list[Job] = []
    succeeded_firms: set[str] = set()
    failed_firms: set[str] = set()
    succeeded_entries = 0
    for board in config.GREENHOUSE_BOARDS:
        firm = board["firm"]
        try:
            jobs = fetch_jobs(board["token"], firm, board["company_type"])
        except Exception:
            logger.exception(
                "greenhouse[%s]: failed to fetch after retries, skipping this "
                "board for the rest of the run",
                firm,
            )
            failed_firms.add(firm)
            continue
        all_jobs.extend(jobs)
        succeeded_firms.add(firm)
        succeeded_entries += 1

    # A firm can have more than one board token (e.g. Optiver has two) --
    # only treat a firm as "fully fetched" this run if every one of its
    # boards succeeded. Otherwise the failed board's still-open postings
    # would look closed to prune_closed_postings below (its jobs are
    # missing from current_jobs) purely because a *different* token for the
    # same firm happened to succeed.
    fetched_boards = succeeded_firms - failed_firms

    logger.info(
        # succeeded_entries/total counts board TOKENS (matches
        # config.GREENHOUSE_BOARDS 1:1); fetched_boards counts distinct
        # FIRMS, which is smaller whenever one firm has multiple tokens
        # (e.g. Optiver) -- comparing that against the token count would
        # make a fully successful run misleadingly print e.g. "14/15".
        "fetched %d jobs total across %d/%d board(s)%s",
        len(all_jobs),
        succeeded_entries,
        len(config.GREENHOUSE_BOARDS),
        f" -- failed: {', '.join(sorted(failed_firms))}" if failed_firms else "",
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
    for category, _keywords in CATEGORIES:
        category_jobs = jobs_by_category[category]
        worksheet = get_worksheet(spreadsheet, category)
        append_jobs(worksheet, category_jobs)
        prune_closed_postings(worksheet, category_jobs, fetched_boards=fetched_boards)
        sort_rows(worksheet)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()
