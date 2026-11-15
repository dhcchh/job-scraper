"""Fetch all configured boards and report health without writing to the sheet."""

import logging
import sys

from jobscraper.cli import BRIEF_LOG_FORMAT, configure_logging, write_step_summary
from jobscraper.job_boards import fetchers

logger = logging.getLogger(__name__)


def check() -> bool:
    """Fetch selected boards and return True if at least one ran and all succeeded."""

    passed: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for result in fetchers.fetch_boards(fetchers.selected_board_sources()):
        firm = result.board["firm"]
        if result.error is not None:
            failed.append((firm, str(result.error)))
            logger.error("%s: FAILED -- %s", firm, result.error)
        else:
            passed.append((firm, len(result.jobs)))
            logger.info("%s: %d jobs", firm, len(result.jobs))

    write_step_summary(_summary_markdown(passed, failed))
    logger.info("%d/%d boards healthy", len(passed), len(passed) + len(failed))
    if not passed and not failed:
        logger.error("No boards selected; check ONLY_FIRMS")
        return False
    return not failed


def _summary_markdown(
    passed: list[tuple[str, int]], failed: list[tuple[str, str]]
) -> str:
    total = len(passed) + len(failed)
    lines = [f"## Board Health: {len(passed)}/{total} healthy", ""]
    if failed:
        lines += ["### Failed", ""]
        lines += [f"- **{firm}**: `{err}`" for firm, err in failed]
        lines.append("")
    if passed:
        lines += ["### Passed", ""]
        lines += [f"- {firm}: {count} jobs" for firm, count in passed]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    configure_logging(BRIEF_LOG_FORMAT)
    sys.exit(0 if check() else 1)
