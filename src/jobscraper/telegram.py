"""Format and send optional Telegram notifications."""

import argparse
import html
import logging
import sys

import requests

from jobscraper import config
from jobscraper.models import Job

logger = logging.getLogger(__name__)

_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"

DIGEST_CATEGORIES: tuple[str, ...] = ("DA/BI", "DS/ML/Algos", "SWE/DE")

_MAX_MESSAGE_LEN = 3800


def build_digest_text(
    new_jobs_by_category: dict[str, list[Job]], spreadsheet_url: str
) -> str | None:
    """Build a grouped PAC-job digest, or return None when empty."""
    category_blocks = []
    total = 0
    for category in DIGEST_CATEGORIES:
        jobs = new_jobs_by_category.get(category) or []
        if not jobs:
            continue
        total += len(jobs)
        entries = _group_entries(jobs)
        category_blocks.append(f"<b>{_escape(category)}</b>\n" + "\n".join(entries))

    if not category_blocks:
        return None

    header = f"\U0001f4e6 {total} new PAC role(s)"
    link = f'<a href="{_escape(spreadsheet_url, quote=True)}">See them in the sheet</a>'
    return "\n\n".join([header, *category_blocks, link])


def _group_entries(jobs: list[Job]) -> list[str]:
    """Group jobs by firm and location in first-seen order."""
    counts: dict[tuple[str, str], int] = {}
    for job in jobs:
        key = (job.board, job.location)
        counts[key] = counts.get(key, 0) + 1
    return [
        _format_group_entry(count, firm, location)
        for (firm, location), count in counts.items()
    ]


def _format_group_entry(count: int, firm: str, location: str) -> str:
    """Format a grouped job count, omitting a blank location."""
    where = f" ({_escape(location)})" if location else ""
    return f"{_escape(firm)} x{count}{where}"


def _escape(text: str, *, quote: bool = False) -> str:
    """Escape text for Telegram HTML."""
    return html.escape(text, quote=quote)


def _chunk(text: str, limit: int | None = None) -> list[str]:
    """Split text at section or line boundaries without exceeding `limit`."""
    if limit is None:
        limit = _MAX_MESSAGE_LEN
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for section in text.split("\n\n"):
        candidate = f"{current}\n\n{section}" if current else section
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(section) <= limit:
            current = section
            continue
        lines = section.split("\n")
        header_line = lines[0]
        for index, line in enumerate(lines):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            if index == 0:
                current = line
                continue
            carried = f"{header_line}\n{line}"
            current = carried if len(carried) <= limit else line
    if current:
        chunks.append(current)
    return chunks


def send_digest(new_jobs_by_category: dict[str, list[Job]], spreadsheet_url: str) -> None:
    """Build and best-effort send the configured job digest."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("telegram digest: not configured, skipping")
        return

    text = build_digest_text(new_jobs_by_category, spreadsheet_url)
    if text is None:
        logger.info("telegram digest: nothing new to report, skipping")
        return

    send_chunks(
        text, config.TELEGRAM_CHAT_ID, config.TELEGRAM_THREAD_ID, label="telegram digest"
    )


_TRACKER_EXPLAINER = (
    "<b>What's the job tracker?</b>\n"
    "Open roles at {total_firms} top-paying quant firms, big tech and leading "
    "startups, in one sheet, refreshed daily."
)
_TRACKER_PITCH = (
    "\U0001f3c6 Reach <b>30,000 points</b> to unlock it: a huge edge for "
    "high-paying roles."
)


def build_new_companies_text(
    new_firms: dict[str, str], spreadsheet_url: str, total_firms: int
) -> str | None:
    """Build the new-company announcement, or return None when empty."""
    if not new_firms:
        return None

    firms_by_type: dict[str, list[str]] = {}
    for firm, company_type in new_firms.items():
        firms_by_type.setdefault(company_type, []).append(firm)
    type_blocks = [
        f"<b>{_escape(company_type)}</b>\n"
        + "\n".join(_escape(firm) for firm in sorted(firms_by_type[company_type]))
        for company_type in sorted(firms_by_type)
    ]

    count = len(new_firms)
    noun = "company" if count == 1 else "companies"
    header = f"\U0001f195 {count} new {noun} added to the job tracker"
    link = (
        "Already unlocked? "
        f'<a href="{_escape(spreadsheet_url, quote=True)}">See their open roles</a>'
    )
    explainer = _TRACKER_EXPLAINER.format(total_firms=total_firms)
    return "\n\n".join([header, *type_blocks, explainer, _TRACKER_PITCH, link])


def send_new_companies(
    new_firms: dict[str, str], spreadsheet_url: str, total_firms: int
) -> None:
    """Best-effort announce newly added firms."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_COMPANIES_CHAT_ID:
        logger.debug("telegram new companies: not configured, skipping")
        return

    text = build_new_companies_text(new_firms, spreadsheet_url, total_firms)
    if text is None:
        logger.info("telegram new companies: no new firms, skipping")
        return

    send_chunks(
        text,
        config.TELEGRAM_COMPANIES_CHAT_ID,
        config.TELEGRAM_COMPANIES_THREAD_ID,
        label="telegram new companies",
    )


def send_chunks(
    text: str, chat_id: int, thread_id: int | None, *, label: str
) -> bool:
    """Send chunks in order, stopping and logging on failure."""
    chunks = _chunk(text)
    for index, chunk in enumerate(chunks, start=1):
        try:
            _send_message(chunk, chat_id, thread_id)
        except Exception:
            logger.exception(
                "%s: failed to send message %d/%d, skipping the rest",
                label,
                index,
                len(chunks),
            )
            return False
    logger.info("%s: sent in %d message(s)", label, len(chunks))
    return True


def _send_message(text: str, chat_id: int, thread_id: int | None) -> None:
    url = _SEND_MESSAGE_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()


def check_connection() -> bool:
    """Send a test message and report whether Telegram accepted it."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print(
            "Not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        )
        return False

    destinations = [("digest", config.TELEGRAM_CHAT_ID, config.TELEGRAM_THREAD_ID)]
    if config.TELEGRAM_COMPANIES_CHAT_ID:
        destinations.append(
            (
                "new companies",
                config.TELEGRAM_COMPANIES_CHAT_ID,
                config.TELEGRAM_COMPANIES_THREAD_ID,
            )
        )
    if config.TELEGRAM_DM_CHAT_ID:
        destinations.append(("role alerts DM", config.TELEGRAM_DM_CHAT_ID, None))

    ok = True
    for name, chat_id, thread_id in destinations:
        where = f"chat {chat_id}"
        if thread_id is not None:
            where += f", thread {thread_id}"
        try:
            _send_message(
                f"✅ latest-job-scraper: test message from `--check` ({name}) -- "
                "if you can see this, the bot can reach this chat.",
                chat_id,
                thread_id,
            )
        except Exception as exc:
            print(f"Failed to send ({name}, {where}): {exc}")
            ok = False
            continue
        print(f"Sent OK ({name}) to {where}.")
    return ok


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Optional Telegram digest for latest-job-scraper. "
        "This module has no standalone run mode of its own -- main.py calls "
        "send_digest() directly -- only --check is available here."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Send a one-off test message to verify TELEGRAM_BOT_TOKEN/"
            "TELEGRAM_CHAT_ID actually work, without waiting for real new "
            "postings."
        ),
    )
    args = parser.parse_args()
    if not args.check:
        parser.print_help()
        sys.exit(1)
    sys.exit(0 if check_connection() else 1)
