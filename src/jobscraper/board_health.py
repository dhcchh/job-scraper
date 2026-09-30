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

    sources = [
        (source, [
            board for board in boards
            if not config.ONLY_FIRMS or board["firm"] in config.ONLY_FIRMS
        ], fetch)
        for source, boards, fetch in fetchers.board_sources()
    ]
    for result in fetchers.fetch_boards(sources):
        firm = result.board["firm"]
        if result.error is not None:
            failed.append((firm, str(result.error)))
            logger.error("%s: FAILED -- %s", firm, result.error)
        else:
            passed.append((firm, len(result.jobs)))
            logger.info("%s: %d jobs", firm, len(result.jobs))

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
