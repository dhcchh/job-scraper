"""DM the operator each new Singapore/Hong Kong data-engineering role.

Separate from telegram.py's group digest: one short message per posting,
with its apply link, to TELEGRAM_DM_CHAT_ID. The job description is added
only for a firm in FIRMS_NEEDING_DESCRIPTION, whose own apply page doesn't
show it. Disabled unless that and TELEGRAM_BOT_TOKEN are set; never raises.
"""

import logging
import re
import textwrap
import time

from jobscraper import config
from jobscraper.enrichment.classify_region import office_countries
from jobscraper.enrichment.classify_roles import (
    _NOT_AN_ENGINEERING_ROLE_RE,
    classify_job,
)
from jobscraper.models import FIRMS_NEEDING_DESCRIPTION, Job
from jobscraper.telegram import _escape, send_chunks

logger = logging.getLogger(__name__)

ALERT_COUNTRIES = frozenset({"Singapore", "Hong Kong"})

_DE_TITLE_RE = re.compile(
    r"\b(?:data engineer(?:ing)?|etl engineer|analytics engineer(?:ing)?)\b",
    re.IGNORECASE,
)
# "Software Engineer (Data)", "Data Platform Engineer", "Backend Developer,
# Data Infrastructure": an engineering title that names data.
_DATA_RE = re.compile(r"\bdata\b", re.IGNORECASE)
_ENGINEER_RE = re.compile(r"\b(?:engineer(?:ing)?|developer)\b", re.IGNORECASE)
_DATA_CENTRE_RE = re.compile(r"\bdata cent(?:er|re)\b", re.IGNORECASE)

MAX_ALERTS_PER_RUN = 15
_MAX_JD_CHARS = 10_000
_MAX_LINE_CHARS = 3_000
_PAUSE_SECONDS = 1.0  # Telegram allows about one message per second per chat


def is_alert_match(job: Job) -> bool:
    """Whether a posting is a data-engineering role with a SG/HK office.

    `classify_job` applies the shared noise vetoes (recruiters, solutions
    architects, ...), so a title the sheet drops never alerts.
    """
    return (
        _is_data_engineering_title(job.title)
        and any(country in ALERT_COUNTRIES for country in office_countries(job.location))
        and classify_job(job) is not None
    )


def _is_data_engineering_title(title: str) -> bool:
    if _NOT_AN_ENGINEERING_ROLE_RE.search(title) or _DATA_CENTRE_RE.search(title):
        return False
    if _DE_TITLE_RE.search(title):
        return True
    return bool(_DATA_RE.search(title) and _ENGINEER_RE.search(title))


def build_alert_text(job: Job) -> str:
    """Format one posting's DM in Telegram HTML."""
    details = [f"Seniority: {job.seniority}"]
    if job.salary:
        details.append(f"Salary: {job.salary}")
    if job.first_published:
        details.append(f"Posted: {job.first_published}")
    header = "\n".join([
        f"\U0001f3af <b>{_escape(job.title)}</b>",
        _escape(f"{job.board} · {job.location}" if job.location else job.board),
        _escape(" · ".join(details)),
        f'<a href="{_escape(job.url, quote=True)}">Apply</a>' if job.url else "",
    ]).rstrip()
    if job.board not in FIRMS_NEEDING_DESCRIPTION or not job.description:
        return header
    return f"{header}\n\n<b>Job description</b>\n{_format_jd(job.description)}"


def _format_jd(jd: str) -> str:
    if len(jd) > _MAX_JD_CHARS:
        jd = jd[:_MAX_JD_CHARS].rstrip() + "\n… (truncated, see the link)"
    # telegram._chunk only splits at line breaks, so no line may exceed a message.
    lines = []
    for line in jd.split("\n"):
        lines.extend(textwrap.wrap(line, _MAX_LINE_CHARS) or [""])
    return _escape("\n".join(lines))


def send_alerts(jobs: list[Job], spreadsheet_url: str) -> None:
    """Best-effort DM each matching posting, capped per run."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_DM_CHAT_ID:
        logger.debug("alerts: not configured, skipping")
        return

    matches = [job for job in jobs if is_alert_match(job)]
    logger.info("alerts: %d new matching role(s)", len(matches))
    if not matches:
        return

    for index, job in enumerate(matches[:MAX_ALERTS_PER_RUN]):
        if index:
            time.sleep(_PAUSE_SECONDS)
        try:
            text = build_alert_text(job)
        except Exception:
            logger.exception("alerts: failed to build DM for %s %s", job.board, job.job_id)
            continue
        send_chunks(text, config.TELEGRAM_DM_CHAT_ID, None, label="alerts")

    overflow = len(matches) - MAX_ALERTS_PER_RUN
    if overflow > 0:
        time.sleep(_PAUSE_SECONDS)
        send_chunks(
            f"+{overflow} more matching role(s) this run: "
            f'<a href="{_escape(spreadsheet_url, quote=True)}">see the sheet</a>',
            config.TELEGRAM_DM_CHAT_ID,
            None,
            label="alerts",
        )


def check_dm(sample_board: str = "okx") -> bool:
    """DM a test message plus one real sample alert; report, don't swallow."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_DM_CHAT_ID:
        print("Not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_DM_CHAT_ID")
        return False
    if not send_chunks(
        "✅ job-scraper: test DM from `alerts --check`. Role alerts will arrive here.",
        config.TELEGRAM_DM_CHAT_ID,
        None,
        label="alerts check",
    ):
        print(f"Failed to DM chat {config.TELEGRAM_DM_CHAT_ID} -- has it pressed Start?")
        return False
    print(f"Sent OK to chat {config.TELEGRAM_DM_CHAT_ID}.")

    from jobscraper.job_boards.fetchers import fetch_greenhouse_jobs

    board = next(b for b in config.GREENHOUSE_BOARDS if b["token"] == sample_board)
    jobs = fetch_greenhouse_jobs(board["token"], board["firm"], board["company_type"])
    job = next((job for job in jobs if is_alert_match(job)), None)
    if job is None:
        print(f"No live {board['firm']} posting matches today; skipped the sample.")
        return True
    sent = send_chunks(
        "[sample] " + build_alert_text(job),
        config.TELEGRAM_DM_CHAT_ID,
        None,
        label="alerts check",
    )
    print(f"Sample alert ({job.title}): {'sent' if sent else 'FAILED'}")
    return sent


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="Per-role Telegram DM alerts.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="DM a test message and one live sample alert to TELEGRAM_DM_CHAT_ID only.",
    )
    if not parser.parse_args().check:
        parser.print_help()
        sys.exit(1)
    sys.exit(0 if check_dm() else 1)
