"""Logging and GitHub Actions output shared by the command entry points."""

import logging
import os

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# For commands whose log is read as a short report, not a timeline.
BRIEF_LOG_FORMAT = "%(levelname)s %(message)s"


def configure_logging(log_format: str = LOG_FORMAT) -> None:
    """Log INFO and above to stderr in a command's chosen format."""
    logging.basicConfig(level=logging.INFO, format=log_format)


def write_step_summary(markdown: str) -> None:
    """Append to `GITHUB_STEP_SUMMARY` when running in GitHub Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write(markdown)
