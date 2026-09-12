"""Entrypoint: fetch every configured board's jobs, classify + route each into
one of jobscraper.classifications.classify_roles's role categories crossed
with classify_region's region, append new postings per (category, region)
tab, prune closed ones.

    uv run python -m jobscraper.main

Loops over GREENHOUSE_BOARDS from config, one board at a time -- no
concurrency, since this runs once a day and there's no rate-limit pressure
at this scale. A board whose fetch fails (greenhouse.client.fetch_jobs
already retries transient errors -- see that module -- so what reaches here
has exhausted those) is logged and skipped rather than aborting the whole
run; its firm is left out of fetched_boards, so prune_closed_postings never
mistakes "we couldn't check this board" for "everything on it just closed"
(see that function's docstring). Every job from a board that DID succeed is
classified by classify_roles.classify_job into a category, then
classify_region.classify_region into PAC or RoW from its (already
normalized -- see job_boards/greenhouse/client.py) location; the pair picks
one of 10 spreadsheet tabs (5 categories x 2 regions, see
classify_region.tab_name), added because the target audience is based in
Singapore and wants local/regional roles easy to find rather than mixed in
with everything else. A job matching no category (HR, Legal, Sales, ...) is
dropped rather than written anywhere -- region is only ever computed for a
job that already has a category, so a noise job's location is never
classified at all. Each job's seniority level (Intern, Senior, Manager,
... or "Unspecified") is computed earlier, at fetch time -- see
classify_seniority.classify_seniority and job_boards/greenhouse/client.py
-- so it's already set on every Job here and just rides along as a column.
Row order in each tab is left entirely to the end user (Sheets' own Data >
Sort range / Filter views) -- this script never reorders existing rows,
only appends new ones and deletes closed ones, so a sort or filter someone
sets up in the sheet survives every run.

Before touching any board, run() opens the spreadsheet and logs its title
and URL -- a startup check, not decoration: `GOOGLE_SHEET_ID` is set
independently in `.env` (local) and the GitHub Actions secret (CI), and
nothing else here cross-checks the two, so a value that drifts on one side
silently writes/prunes real data against the wrong file while still
reporting a clean run (this has happened once already -- see CLAUDE.md's
Known gaps). Logging the sheet's own identity makes that visible to anyone
who actually reads the run log, even though it can't be checked
automatically from here.

At the end of the run, _build_summary_markdown/_write_job_summary append a
run summary -- boards fetched/failed, jobs fetched/classified/filtered,
and per-tab appended/pruned counts -- to `$GITHUB_STEP_SUMMARY`. That
variable is only set inside a GitHub Actions run (it names a file the
runner renders on the workflow's summary page); _write_job_summary no-ops
when it's unset, which is always true for a local `uv run python -m
jobscraper.main`.

TODO(extension points), roughly in the order you'll likely reach for them:
  - more boards: add e.g. jobscraper/job_boards/lever/client.py with its own
    fetch_jobs() -> list[Job], and extend the fetch loop below the same way.
  - more/edited categories: see classifications/classify_roles.py's
    CATEGORIES -- add a keyword there, or a whole new category + matching
    spreadsheet tab (x2, one per region -- see classify_region.tab_name).
    Same idea for seniority levels, in classify_seniority.py, and for the
    PAC/RoW split itself in classify_region.py's _PAC_COUNTRIES.
"""

import logging
import os

from jobscraper import config
from jobscraper.classifications.classify_region import (
    REGIONS,
    classify_region,
    tab_name,
)
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
    spreadsheet = get_spreadsheet()
    logger.info(
        "writing to spreadsheet %r (%s)", spreadsheet.title, spreadsheet.url
    )

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

    jobs_by_tab: dict[str, list[Job]] = {
        tab_name(category, region): []
        for category, _keywords in CATEGORIES
        for region in REGIONS
    }
    noise_count = 0
    for job in all_jobs:
        category = classify_job(job)
        if category is None:
            noise_count += 1
            continue
        region = classify_region(job.location)
        jobs_by_tab[tab_name(category, region)].append(job)

    logger.info(
        "classified %d job(s) across %d tab(s), filtered %d as noise",
        len(all_jobs) - noise_count,
        len(jobs_by_tab),
        noise_count,
    )

    tab_stats: dict[str, tuple[int, int, int]] = {}
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            tab_jobs = jobs_by_tab[tab]
            worksheet = get_worksheet(spreadsheet, tab)
            appended = append_jobs(worksheet, tab_jobs)
            pruned = prune_closed_postings(
                worksheet, tab_jobs, fetched_boards=fetched_boards
            )
            tab_stats[tab] = (len(tab_jobs), appended, pruned)

    markdown = _build_summary_markdown(
        spreadsheet_title=spreadsheet.title,
        spreadsheet_url=spreadsheet.url,
        succeeded_firms=succeeded_firms,
        failed_firms=failed_firms,
        total_boards=len(config.GREENHOUSE_BOARDS),
        total_jobs=len(all_jobs),
        noise_count=noise_count,
        tab_stats=tab_stats,
    )
    _write_job_summary(markdown)


def _build_summary_markdown(
    *,
    spreadsheet_title: str,
    spreadsheet_url: str,
    succeeded_firms: set[str],
    failed_firms: set[str],
    total_boards: int,
    total_jobs: int,
    noise_count: int,
    tab_stats: dict[str, tuple[int, int, int]],
) -> str:
    """The run summary as GitHub-flavored Markdown, for _write_job_summary.

    Kept separate from _write_job_summary so it can be tested as a plain
    string-in/string-out function, with no env var or filesystem involved.
    """
    total_appended = sum(appended for _count, appended, _pruned in tab_stats.values())
    total_pruned = sum(pruned for _count, _appended, pruned in tab_stats.values())

    lines = [
        f"## Job scraper run: {'success' if not failed_firms else 'partial failure'}",
        "",
        f"Spreadsheet: [{spreadsheet_title}]({spreadsheet_url})",
        "",
        f"- Boards fetched: {len(succeeded_firms)}/{total_boards}"
        + (f" -- failed: {', '.join(sorted(failed_firms))}" if failed_firms else ""),
        f"- Jobs fetched: {total_jobs} ({noise_count} filtered as noise)",
        f"- Appended: {total_appended} new row(s)",
        f"- Pruned: {total_pruned} closed posting(s)",
        "",
        "| Tab | Jobs this run | Appended | Pruned |",
        "| --- | ---: | ---: | ---: |",
    ]
    for tab, (job_count, appended, pruned) in sorted(tab_stats.items()):
        lines.append(f"| {tab} | {job_count} | {appended} | {pruned} |")

    return "\n".join(lines) + "\n"


def _write_job_summary(markdown: str) -> None:
    """Append markdown to $GITHUB_STEP_SUMMARY, if set.

    That variable only exists inside a GitHub Actions run -- it names a
    file the runner renders on the workflow's own summary page. A local
    `uv run python -m jobscraper.main` (and every test) has no such env
    var, so this is a no-op there rather than an error.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a") as f:
        f.write(markdown)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()
