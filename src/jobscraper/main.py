"""Fetch configured boards, route jobs to sheet tabs, and prune closed posts."""

import logging
import os
from collections import Counter

from jobscraper import config
from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    is_excluded,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.job_boards.fetchers import (
    fetch_ashby_jobs,
    fetch_eightfold_jobs,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    fetch_recruitee_jobs,
    fetch_smartrecruiters_jobs,
    fetch_workday_jobs,
)
from jobscraper.models import Job
from jobscraper.sheets.supported_companies import sync_and_announce
from jobscraper.sheets.writer import (
    PrunePlan,
    append_jobs,
    apply_prune,
    get_spreadsheet,
    get_worksheet,
    held_back_prunes,
    plan_prune,
)
from jobscraper.telegram import DIGEST_CATEGORIES, send_digest

logger = logging.getLogger(__name__)


def run() -> None:
    spreadsheet = get_spreadsheet()
    logger.info(
        "writing to spreadsheet %r (%s)", spreadsheet.title, spreadsheet.url
    )
    sync_and_announce(spreadsheet)

    board_sources = (
        ("greenhouse", config.GREENHOUSE_BOARDS, fetch_greenhouse_jobs),
        ("lever", config.LEVER_BOARDS, fetch_lever_jobs),
        ("ashby", config.ASHBY_BOARDS, fetch_ashby_jobs),
        ("smartrecruiters", config.SMARTRECRUITERS_BOARDS, fetch_smartrecruiters_jobs),
        ("eightfold", config.EIGHTFOLD_BOARDS, fetch_eightfold_jobs),
        ("workday", config.WORKDAY_BOARDS, fetch_workday_jobs),
        ("recruitee", config.RECRUITEE_BOARDS, fetch_recruitee_jobs),
    )

    all_jobs: list[Job] = []
    succeeded_firms: set[str] = set()
    failed_firms: set[str] = set()
    succeeded_entries = 0
    for source, boards, fetch in board_sources:
        for board in boards:
            firm = board["firm"]
            try:
                kwargs = (
                    {"location_scope": board["location_scope"]}
                    if source == "workday" and board.get("location_scope")
                    else {}
                )
                jobs = fetch(board["token"], firm, board["company_type"], **kwargs)
            except Exception:
                logger.exception(
                    "%s[%s]: failed to fetch after retries, skipping this "
                    "board for the rest of the run",
                    source,
                    firm,
                )
                failed_firms.add(firm)
                continue
            all_jobs.extend(jobs)
            succeeded_firms.add(firm)
            succeeded_entries += 1

    total_boards = sum(len(boards) for _source, boards, _fetch in board_sources)

    # Any failed token makes that firm's postings unsafe to prune.
    fetched_boards = succeeded_firms - failed_firms

    logger.info(
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
    excluded_count = 0
    for job in all_jobs:
        category = classify_job(job)
        if category is None:
            noise_count += 1
            continue
        if is_excluded(job.location):
            excluded_count += 1
            continue
        region = classify_region(job.location)
        jobs_by_tab[tab_name(category, region)].append(job)

    logger.info(
        "classified %d job(s) across %d tab(s), filtered %d as noise, "
        "%d as excluded-country",
        len(all_jobs) - noise_count - excluded_count,
        len(jobs_by_tab),
        noise_count,
        excluded_count,
    )

    newly_appended_by_tab: dict[str, list[Job]] = {}
    prune_plans: dict[str, PrunePlan] = {}
    for category, _keywords in CATEGORIES:
        for region in REGIONS:
            tab = tab_name(category, region)
            tab_jobs = jobs_by_tab[tab]
            worksheet = get_worksheet(spreadsheet, tab)
            newly_appended_by_tab[tab] = append_jobs(worksheet, tab_jobs)
            prune_plans[tab] = plan_prune(
                worksheet, tab_jobs, fetched_boards=fetched_boards
            )

    # Every tab is planned before any is pruned, so the guard sees a firm's
    # whole footprint rather than one tab's slice of it.
    held_back: dict[str, str] = {}
    for firm, reason in held_back_prunes(
        prune_plans.values(),
        fetched_counts=Counter(job.board for job in all_jobs),
        appended_counts=Counter(
            job.board for jobs in newly_appended_by_tab.values() for job in jobs
        ),
    ).items():
        if firm in config.FORCE_PRUNE_FIRMS:
            logger.warning("prune guard overridden for %s: %s", firm, reason)
        else:
            logger.warning("prune held back for %s: %s", firm, reason)
            held_back[firm] = reason

    tab_stats: dict[str, tuple[int, int, int]] = {}
    for tab, plan in prune_plans.items():
        pruned = apply_prune(plan, held_back)
        tab_stats[tab] = (len(jobs_by_tab[tab]), len(newly_appended_by_tab[tab]), pruned)

    markdown = _build_summary_markdown(
        spreadsheet_title=spreadsheet.title,
        spreadsheet_url=spreadsheet.url,
        succeeded_firms=succeeded_firms,
        failed_firms=failed_firms,
        total_boards=total_boards,
        total_jobs=len(all_jobs),
        noise_count=noise_count,
        excluded_count=excluded_count,
        tab_stats=tab_stats,
        held_back=held_back,
    )
    _write_job_summary(markdown)

    send_digest(
        {
            category: newly_appended_by_tab.get(tab_name(category, "PAC"), [])
            for category in DIGEST_CATEGORIES
        },
        spreadsheet_url=spreadsheet.url,
    )

    problems = []
    if failed_firms:
        problems.append("failed board fetches: " + ", ".join(sorted(failed_firms)))
    if held_back:
        problems.append("prune held back for: " + ", ".join(sorted(held_back)))
    if problems:
        raise RuntimeError("scrape completed with " + "; ".join(problems))


def _build_summary_markdown(
    *,
    spreadsheet_title: str,
    spreadsheet_url: str,
    succeeded_firms: set[str],
    failed_firms: set[str],
    total_boards: int,
    total_jobs: int,
    noise_count: int,
    excluded_count: int,
    tab_stats: dict[str, tuple[int, int, int]],
    held_back: dict[str, str] | None = None,
) -> str:
    """Build the GitHub Actions run summary."""
    held_back = held_back or {}
    total_appended = sum(appended for _count, appended, _pruned in tab_stats.values())
    total_pruned = sum(pruned for _count, _appended, pruned in tab_stats.values())

    lines = [
        "## Job scraper run: "
        + ("partial failure" if failed_firms or held_back else "success"),
        "",
        f"Spreadsheet: [{spreadsheet_title}]({spreadsheet_url})",
        "",
        f"- Boards fetched: {len(succeeded_firms)}/{total_boards}"
        + (f" -- failed: {', '.join(sorted(failed_firms))}" if failed_firms else ""),
        f"- Jobs fetched: {total_jobs} ({noise_count} filtered as noise, "
        f"{excluded_count} excluded by country)",
        f"- Appended: {total_appended} new row(s)",
        f"- Pruned: {total_pruned} closed posting(s)"
        + (f" -- held back for: {', '.join(sorted(held_back))}" if held_back else ""),
        "",
        "| Tab | Jobs this run | Appended | Pruned |",
        "| --- | ---: | ---: | ---: |",
    ]
    for tab, (job_count, appended, pruned) in sorted(tab_stats.items()):
        lines.append(f"| {tab} | {job_count} | {appended} | {pruned} |")

    if held_back:
        lines += [
            "",
            "### Prune held back",
            "",
            "These firms' closed-looking rows were kept: the drop looks more "
            "like a broken fetch than real closures. If the closures are real, "
            'rerun "Daily Scrape (Append)" with `force_prune_firms` set to the '
            "firm name(s).",
            "",
        ]
        lines += [f"- {firm}: {reason}" for firm, reason in sorted(held_back.items())]

    return "\n".join(lines) + "\n"


def _write_job_summary(markdown: str) -> None:
    """Append to `GITHUB_STEP_SUMMARY` when running in GitHub Actions."""
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
