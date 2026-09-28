"""Fetch all configured boards and report health without writing to the sheet."""

import logging
import os
import sys

from jobscraper import config
from jobscraper.job_boards.fetchers import (
    fetch_ashby_jobs,
    fetch_eightfold_jobs,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    fetch_recruitee_jobs,
    fetch_smartrecruiters_jobs,
    fetch_workday_jobs,
)

logger = logging.getLogger(__name__)


def check() -> bool:
    """Fetch every configured board and return True if all succeeded."""
    board_sources = (
        ("greenhouse", config.GREENHOUSE_BOARDS, fetch_greenhouse_jobs),
        ("lever", config.LEVER_BOARDS, fetch_lever_jobs),
        ("ashby", config.ASHBY_BOARDS, fetch_ashby_jobs),
        ("smartrecruiters", config.SMARTRECRUITERS_BOARDS, fetch_smartrecruiters_jobs),
        ("eightfold", config.EIGHTFOLD_BOARDS, fetch_eightfold_jobs),
        ("workday", config.WORKDAY_BOARDS, fetch_workday_jobs),
        ("recruitee", config.RECRUITEE_BOARDS, fetch_recruitee_jobs),
    )

    passed: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for source, boards, fetch in board_sources:
        for board in boards:
            firm = board["firm"]
            if config.ONLY_FIRMS and firm not in config.ONLY_FIRMS:
                continue
            kwargs = (
                {"location_scope": board["location_scope"]}
                if source == "workday" and board.get("location_scope")
                else {}
            )
            try:
                jobs = fetch(board["token"], firm, board["company_type"], **kwargs)
                passed.append((firm, len(jobs)))
                logger.info("%s: %d jobs", firm, len(jobs))
            except Exception as exc:
                failed.append((firm, str(exc)))
                logger.error("%s: FAILED -- %s", firm, exc)

    _write_summary(passed, failed)
    logger.info("%d/%d boards healthy", len(passed), len(passed) + len(failed))
    return not failed


def _write_summary(
    passed: list[tuple[str, int]], failed: list[tuple[str, str]]
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    total = len(passed) + len(failed)
    with open(summary_path, "a") as f:
        f.write(f"## Board Health: {len(passed)}/{total} healthy\n\n")
        if failed:
            f.write("### Failed\n\n")
            for firm, err in failed:
                f.write(f"- **{firm}**: `{err}`\n")
            f.write("\n")
        if passed:
            f.write("### Passed\n\n")
            for firm, count in passed:
                f.write(f"- {firm}: {count} jobs\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(0 if check() else 1)
