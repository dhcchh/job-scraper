"""Entrypoint: fetch every configured board's jobs, classify + route each into
one of jobscraper.enrichment.classify_roles's role categories crossed
with classify_region's region, append new postings per (category, region)
tab, prune closed ones.

    uv run python -m jobscraper.main

Loops over GREENHOUSE_BOARDS, then LEVER_BOARDS, then ASHBY_BOARDS, then
SMARTRECRUITERS_BOARDS, from config -- one board at a time within each, no
concurrency, since this runs once a day and there's no rate-limit pressure
at this scale. All four loops feed the same
all_jobs/succeeded_firms/failed_firms accumulators, so a firm's board type
is otherwise invisible past this point -- prune_closed_postings only ever
reasons about firms. A board whose fetch fails
(greenhouse.client.fetch_greenhouse_jobs/lever.client.fetch_lever_jobs/
ashby.client.fetch_ashby_jobs/smartrecruiters.client.fetch_smartrecruiters_jobs
already retry transient errors -- see those modules -- so what reaches here
has exhausted those) is skipped for pruning; its
firm is left out of fetched_boards, so prune_closed_postings never mistakes
"we couldn't check this board" for "everything on it just closed".
Successful boards are still updated, then the process fails after writing
its summary so GitHub Actions cannot report incomplete data as healthy.
Every job from a board that DID succeed is
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
could otherwise silently write/prune real data against the wrong file while
still reporting a clean run. If the optional `GOOGLE_SHEET_TITLE` is also
set, sheets.writer.get_spreadsheet() raises before this line runs when the
opened spreadsheet's title doesn't match, turning that drift into a hard
failure instead of something only visible to someone who reads the log.

At the end of the run, _build_summary_markdown/_write_job_summary append a
run summary -- boards fetched/failed, jobs fetched/classified/filtered,
and per-tab appended/pruned counts -- to `$GITHUB_STEP_SUMMARY`. That
variable is only set inside a GitHub Actions run (it names a file the
runner renders on the workflow's summary page); _write_job_summary no-ops
when it's unset, which is always true for a local `uv run python -m
jobscraper.main`.

telegram.send_digest is called right after, with each of DA/BI, DS/ML/Algos,
and SWE/DE's newly appended PAC-tab Jobs (from append_jobs' return value --
not tab_jobs, which is every job classified into that tab this run, appended
or not) plus this run's already-opened spreadsheet.url, for the digest's
trailing link. Unconfigured or nothing new, it no-ops; see telegram.py for
why PAC/these-three-categories only, why the message is grouped counts (not
a full per-job listing) linking back to the sheet rather than to each job
directly, and why this is called even when failed_firms will fail the run
below.

TODO(extension points), roughly in the order you'll likely reach for them:
  - more boards: add a client module with its own fetch_<board>_jobs() ->
    list[Job], and extend the fetch loop below the same way Greenhouse,
    Lever, Ashby, and SmartRecruiters already are.
  - more/edited categories: see enrichment/classify_roles.py's
    CATEGORIES -- add a keyword there, or a whole new category + matching
    spreadsheet tab (x2, one per region -- see classify_region.tab_name).
    Same idea for seniority levels, in classify_seniority.py, and for the
    PAC/RoW split itself in classify_region.py's _PAC_COUNTRIES.
"""

import logging
import os

from jobscraper import config
from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.job_boards.ashby.client import fetch_ashby_jobs
from jobscraper.job_boards.greenhouse.client import fetch_greenhouse_jobs
from jobscraper.job_boards.lever.client import fetch_lever_jobs
from jobscraper.job_boards.smartrecruiters.client import fetch_smartrecruiters_jobs
from jobscraper.models import Job
from jobscraper.sheets.writer import (
    append_jobs,
    get_spreadsheet,
    get_worksheet,
    prune_closed_postings,
)
from jobscraper.telegram import DIGEST_CATEGORIES, send_digest

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
            jobs = fetch_greenhouse_jobs(board["token"], firm, board["company_type"])
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

    for board in config.LEVER_BOARDS:
        firm = board["firm"]
        try:
            jobs = fetch_lever_jobs(board["token"], firm, board["company_type"])
        except Exception:
            logger.exception(
                "lever[%s]: failed to fetch after retries, skipping this "
                "board for the rest of the run",
                firm,
            )
            failed_firms.add(firm)
            continue
        all_jobs.extend(jobs)
        succeeded_firms.add(firm)
        succeeded_entries += 1

    for board in config.ASHBY_BOARDS:
        firm = board["firm"]
        try:
            jobs = fetch_ashby_jobs(board["token"], firm, board["company_type"])
        except Exception:
            logger.exception(
                "ashby[%s]: failed to fetch after retries, skipping this "
                "board for the rest of the run",
                firm,
            )
            failed_firms.add(firm)
            continue
        all_jobs.extend(jobs)
        succeeded_firms.add(firm)
        succeeded_entries += 1

    for board in config.SMARTRECRUITERS_BOARDS:
        firm = board["firm"]
        try:
            jobs = fetch_smartrecruiters_jobs(board["token"], firm, board["company_type"])
        except Exception:
            logger.exception(
                "smartrecruiters[%s]: failed to fetch after retries, skipping this "
                "board for the rest of the run",
                firm,
            )
            failed_firms.add(firm)
            continue
        all_jobs.extend(jobs)
        succeeded_firms.add(firm)
        succeeded_entries += 1

    total_boards = (
        len(config.GREENHOUSE_BOARDS)
        + len(config.LEVER_BOARDS)
        + len(config.ASHBY_BOARDS)
        + len(config.SMARTRECRUITERS_BOARDS)
    )

    # A firm can have more than one board token (e.g. Optiver has two) --
    # only treat a firm as "fully fetched" this run if every one of its
    # boards succeeded. Otherwise the failed board's still-open postings
    # would look closed to prune_closed_postings below (its jobs are
    # missing from current_jobs) purely because a *different* token for the
    # same firm happened to succeed.
    fetched_boards = succeeded_firms - failed_firms

    logger.info(
        # succeeded_entries/total counts board TOKENS across
        # GREENHOUSE_BOARDS, LEVER_BOARDS, and ASHBY_BOARDS; fetched_boards
        # counts distinct FIRMS, which is smaller whenever one firm has
        # multiple tokens
        # (e.g. Optiver) -- comparing that against the token count would
        # make a fully successful run misleadingly print e.g. "14/15".
        "fetched %d jobs total across %d/%d board(s)%s",
        len(all_jobs),
        succeeded_entries,
        total_boards,
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
    newly_appended_by_tab: dict[str, list[Job]] = {}
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            tab_jobs = jobs_by_tab[tab]
            worksheet = get_worksheet(spreadsheet, tab)
            appended = append_jobs(worksheet, tab_jobs)
            pruned = prune_closed_postings(
                worksheet, tab_jobs, fetched_boards=fetched_boards
            )
            tab_stats[tab] = (len(tab_jobs), len(appended), pruned)
            newly_appended_by_tab[tab] = appended

    markdown = _build_summary_markdown(
        spreadsheet_title=spreadsheet.title,
        spreadsheet_url=spreadsheet.url,
        succeeded_firms=succeeded_firms,
        failed_firms=failed_firms,
        total_boards=total_boards,
        total_jobs=len(all_jobs),
        noise_count=noise_count,
        tab_stats=tab_stats,
    )
    _write_job_summary(markdown)

    # Sent regardless of failed_firms below, same reasoning as writing the
    # summary above: a board failing elsewhere doesn't make the postings
    # that WERE newly appended into these PAC tabs any less real, and
    # withholding the digest until some future fully-clean run could delay
    # it indefinitely for a chronically flaky board. PAC-only, three
    # categories -- see telegram.py.
    send_digest(
        {
            category: newly_appended_by_tab.get(tab_name(category, "PAC"), [])
            for category in DIGEST_CATEGORIES
        },
        spreadsheet_url=spreadsheet.url,
    )

    # Keep successful boards current and avoid pruning a failed board, but do
    # not let the workflow look healthy when its result is incomplete. The
    # summary is written first so the GitHub Actions failure has enough detail
    # to diagnose the affected firms.
    if failed_firms:
        raise RuntimeError(
            "scrape completed with failed board fetches: "
            + ", ".join(sorted(failed_firms))
        )


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
