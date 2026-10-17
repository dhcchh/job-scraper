"""Fetch configured boards, route jobs to sheet tabs, and prune closed posts."""

import logging
import os
from collections import Counter
from collections.abc import Mapping

from jobscraper import config
from jobscraper.alerts import send_alerts
from jobscraper.coverage import coverage_markdown, unmatched_titles, unresolved_locations
from jobscraper.enrichment.classify_region import (
    REGIONS,
    classify_region,
    is_excluded,
    tab_name,
)
from jobscraper.enrichment.classify_roles import CATEGORIES, classify_job
from jobscraper.job_boards import fetchers
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

# Pause before refetching the boards that failed. Millennium's Eightfold
# duplicate-ID failure on 2026-10-17 cleared on a run three minutes later.
BOARD_RETRY_DELAY_SECONDS = 60


def run() -> None:
    spreadsheet = get_spreadsheet()
    logger.info(
        "writing to spreadsheet %r (%s)", spreadsheet.title, spreadsheet.url
    )
    sync_and_announce(spreadsheet)

    board_sources = fetchers.selected_board_sources()

    all_jobs: list[Job] = []
    succeeded_firms: set[str] = set()
    failed_firms: dict[str, str] = {}
    succeeded_entries = 0
    for result in fetchers.fetch_boards(
        board_sources, retry_failed_after=BOARD_RETRY_DELAY_SECONDS
    ):
        firm = result.board["firm"]
        if result.error is not None:
            logger.error(
                "%s[%s]: failed to fetch after retries, skipping this "
                "board for the rest of the run",
                result.source,
                firm,
                exc_info=result.error,
            )
            reason = f"{result.source}: {fetchers.describe_error(result.error)}"
            failed_firms[firm] = (
                f"{failed_firms[firm]}; {reason}" if firm in failed_firms else reason
            )
            continue
        all_jobs.extend(result.jobs)
        succeeded_firms.add(firm)
        succeeded_entries += 1

    total_boards = sum(len(boards) for _source, boards, _fetch in board_sources)

    # Any failed token makes that firm's postings unsafe to prune.
    fetched_firms = succeeded_firms - failed_firms.keys()

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
    noise_jobs: list[Job] = []
    excluded_count = 0
    for job in all_jobs:
        category = classify_job(job)
        if category is None:
            noise_jobs.append(job)
            continue
        if is_excluded(job.location):
            excluded_count += 1
            continue
        region = classify_region(job.location)
        jobs_by_tab[tab_name(category, region)].append(job)

    logger.info(
        "classified %d job(s) across %d tab(s), filtered %d as noise, "
        "%d as excluded-country",
        len(all_jobs) - len(noise_jobs) - excluded_count,
        len(jobs_by_tab),
        len(noise_jobs),
        excluded_count,
    )

    prune_plans: dict[str, PrunePlan] = {
        tab: plan_prune(
            get_worksheet(spreadsheet, tab), tab_jobs, fetched_boards=fetched_firms
        )
        for tab, tab_jobs in jobs_by_tab.items()
    }

    # Check the whole existing footprint before any job tab is changed.
    held_back: dict[str, str] = {}
    for firm, reason in held_back_prunes(
        prune_plans.values(),
        fetched_counts=Counter(job.board for job in all_jobs),
    ).items():
        if firm in config.FORCE_PRUNE_FIRMS:
            logger.warning("prune guard overridden for %s: %s", firm, reason)
        else:
            logger.warning("prune held back for %s: %s", firm, reason)
            held_back[firm] = reason

    newly_appended_by_tab: dict[str, list[Job]] = {}
    for tab, plan in prune_plans.items():
        newly_appended_by_tab[tab] = append_jobs(
            plan.worksheet,
            [job for job in jobs_by_tab[tab] if job.board not in held_back],
            existing_keys=plan.keys,
        )
    # Reread a tab that got appends: preserved sort settings may have moved
    # its rows. An untouched tab's plan still holds.
    prune_plans = {
        tab: (
            plan_prune(plan.worksheet, jobs_by_tab[tab], fetched_firms)
            if newly_appended_by_tab[tab]
            else plan
        )
        for tab, plan in prune_plans.items()
    }

    tab_stats: dict[str, tuple[int, int, int]] = {}
    for tab, plan in prune_plans.items():
        pruned = apply_prune(plan, held_back)
        tab_stats[tab] = (len(jobs_by_tab[tab]), len(newly_appended_by_tab[tab]), pruned)

    markdown = _build_summary_markdown(
        spreadsheet_title=spreadsheet.title,
        spreadsheet_url=spreadsheet.url,
        succeeded_entries=succeeded_entries,
        failed_firms=failed_firms,
        total_boards=total_boards,
        total_jobs=len(all_jobs),
        noise_count=len(noise_jobs),
        excluded_count=excluded_count,
        tab_stats=tab_stats,
        held_back=held_back,
    )
    locations = unresolved_locations(
        job for jobs in newly_appended_by_tab.values() for job in jobs
    )
    titles = unmatched_titles(noise_jobs)
    logger.info(
        "coverage: %d unresolved office(s) in new rows, %d unmatched title phrase(s)",
        len(locations),
        len(titles),
    )
    _write_job_summary(markdown + coverage_markdown(locations, titles))

    send_digest(
        {
            category: newly_appended_by_tab.get(tab_name(category, "PAC"), [])
            for category in DIGEST_CATEGORIES
        },
        spreadsheet_url=spreadsheet.url,
    )
    # SG/HK are PAC, and a data-engineering title can land on any category's
    # tab (DS/ML/Algos wins "ML Data Engineer"), so every new PAC row is checked.
    send_alerts(
        [
            job
            for category, _keywords in CATEGORIES
            for job in newly_appended_by_tab.get(tab_name(category, "PAC"), [])
        ],
        spreadsheet_url=spreadsheet.url,
    )

    problems = []
    if failed_firms:
        problems.append("failed board fetches: " + _with_reasons(failed_firms))
    if held_back:
        problems.append("prune held back for: " + _with_reasons(held_back))
    if problems:
        raise RuntimeError("scrape completed with " + "; ".join(problems))


def _with_reasons(reasons: Mapping[str, str]) -> str:
    return ", ".join(f"{firm} ({reason})" for firm, reason in sorted(reasons.items()))


def _build_summary_markdown(
    *,
    spreadsheet_title: str,
    spreadsheet_url: str,
    succeeded_entries: int,
    failed_firms: Mapping[str, str],
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
        f"- Boards fetched: {succeeded_entries}/{total_boards}"
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

    if failed_firms:
        lines += [
            "",
            "### Failed boards",
            "",
            "These boards failed again on a delayed retry. Their firms' rows were "
            "kept, and nothing was appended or pruned for them. A one-off failure "
            "usually clears on the next run. If a board fails several runs in a "
            "row, check its token and whether the firm changed provider.",
            "",
        ]
        lines += [f"- {firm}: {reason}" for firm, reason in sorted(failed_firms.items())]

    if held_back:
        lines += [
            "",
            "### Prune held back",
            "",
            "These firms' existing rows were kept and new rows were held back: "
            "the drop looks more "
            "like a broken fetch than real closures. A board that keeps returning "
            "0 postings may have moved to another provider or stopped listing "
            "jobs. If the closures are real, "
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
