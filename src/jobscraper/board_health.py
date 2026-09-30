"""Fetch all configured boards and report health without writing to the sheet."""

import logging
import os
import sys

from jobscraper import config
from jobscraper.job_boards import fetchers

logger = logging.getLogger(__name__)


def check() -> bool:
    """Fetch selected boards and return True if at least one ran and all succeeded."""

    passed: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for source, boards, fetch in fetchers.board_sources():
        for board in boards:
            firm = board["firm"]
            if config.ONLY_FIRMS and firm not in config.ONLY_FIRMS:
                continue
            try:
                jobs = fetchers.fetch_board(source, board, fetch)
                passed.append((firm, len(jobs)))
                logger.info("%s: %d jobs", firm, len(jobs))
            except Exception as exc:
                failed.append((firm, str(exc)))
                logger.error("%s: FAILED -- %s", firm, exc)

    _write_summary(passed, failed)
    logger.info("%d/%d boards healthy", len(passed), len(passed) + len(failed))
    if not passed and not failed:
        logger.error("No boards selected; check ONLY_FIRMS")
        return False
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
