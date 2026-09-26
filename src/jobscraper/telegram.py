"""Optional Telegram digest of newly appended DA/BI, DS/ML/Algos, and SWE/DE
(PAC-only) postings, sent as one batched message at the end of a run.

Scoped to those three categories and to PAC (not QT/QR, not SRE/DevOps, not
RoW) on purpose -- see CLAUDE.md's Open work item this closes and
classify_region.py's docstring: the target audience is Singapore-based, and
those three categories are the ones it cares about a same-day nudge for.
main.py is what does that filtering (from append_jobs' now-returned newly
appended Job lists, keyed by tab_name(category, "PAC")) -- this module only
formats and sends whatever dict it's handed.

Deliberately a *count* digest, not a listing: a bold category header
followed by one terse "Firm xN (Location)" line per (firm, location) group
under it, e.g.

    <b>SWE/DE</b>
    Grab x3 (Singapore)
    OpenAI x2 (Singapore)

not one line per job with its own title and apply link, and not the earlier
per-group full sentence this replaced ("3 new SWE/DE roles from Grab
available in Singapore" for every group) -- that read fine at a handful of
groups but turned into a long, hard-to-scan wall of near-identical sentences
on a day with many firms, repeating the category name and "new .../roles
from/available in" boilerplate on every single line. The terser token still
gets its own line per group (rather than being comma-joined onto the
category header's line) so `_chunk` can keep splitting a category with many
groups line-by-line instead of being handed one unsplittable run-on line. An
even earlier version put every new job's title and a direct apply link
straight in the message -- that gave everything away up front and gave a
reader no reason to ever open the tracker sheet itself. The message ends
with one link to the spreadsheet itself (not a per-job/per-category deep
link) so the reader goes there for titles, exact locations, and the real
apply link.

Config-gated and best-effort: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset (the
default -- see config.py) makes send_digest a silent no-op, and any failure
to reach Telegram is logged, never raised. A notification going out is not
important enough to fail a run that otherwise successfully wrote to the
sheet -- that's what actually matters, and main.py already fails the run
for real (fetch) failures separately.

Sent via a raw HTTP POST to Telegram's Bot API rather than a bot framework
(e.g. aiogram, used by the sibling data-career-hustler-bot) -- this fires
one message and exits; there's no dispatcher, no updates to receive, no
long-running process to justify one.

    uv run python -m jobscraper.telegram --check

sends a one-off test message via check_connection() to verify
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID actually work, without waiting for a
real run to have new PAC postings to report -- see check_connection's own
docstring for how its error handling deliberately differs from send_digest's.

send_new_companies is a second, separate message: a one-off announcement
that a firm was added to the tracker (a new *_boards.toml entry), sent to
its own TELEGRAM_COMPANIES_CHAT_ID/TELEGRAM_COMPANIES_THREAD_ID topic by the
same bot. Which firms count as new is decided by
sheets.supported_companies.sync_supported_companies (a firm in config that
the "Supported companies" tab didn't list yet) -- this module only formats
and sends. Same config-gated, never-raises contract as send_digest.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys

import requests

from jobscraper import config
from jobscraper.models import Job

logger = logging.getLogger(__name__)

_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"

# The three categories this digest covers, in the order CLAUDE.md's open-work
# item lists them -- QT/QR and SRE/DevOps are deliberately left out, not
# forgotten (see the module docstring).
DIGEST_CATEGORIES: tuple[str, ...] = ("DA/BI", "DS/ML/Algos", "SWE/DE")

# Telegram's hard cap on one message is 4096 UTF-16 code units. Staying well
# under it (the same margin data-career-hustler-bot's own job digest uses)
# means a long day's digest still fits in one message the vast majority of
# the time, and _chunk only has to split on the rare day it doesn't.
_MAX_MESSAGE_LEN = 3800


def build_digest_text(
    new_jobs_by_category: dict[str, list[Job]], spreadsheet_url: str
) -> str | None:
    """The digest message body, or None if there's nothing new to report.

    `new_jobs_by_category` is keyed by DIGEST_CATEGORIES entries -- plain
    category names, already filtered to one region (PAC) and to jobs
    actually appended this run, not every job classified into that
    category/region today. A category that's absent or empty is left out of
    the message rather than shown as "0 new roles" noise.

    Within a category, jobs are grouped by (firm, location) into one terse
    "Firm xN (Location)" line each, under a bold category header -- see the
    module docstring for why (a count digest, and a compact one). Each group
    stays its own line, not folded onto the category header's line, so
    `_chunk` can still split a category with many groups line-by-line
    instead of being handed one unsplittable run-on line. `spreadsheet_url`
    is appended as one link at the end, only when there's actually something
    new to point at.
    """
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
    """One "Firm xN (Location)" token per distinct (firm, location) combo in
    `jobs`, in first-seen order."""
    counts: dict[tuple[str, str], int] = {}
    for job in jobs:
        key = (job.board, job.location)
        counts[key] = counts.get(key, 0) + 1
    return [
        _format_group_entry(count, firm, location)
        for (firm, location), count in counts.items()
    ]


def _format_group_entry(count: int, firm: str, location: str) -> str:
    """"Grab x3 (Singapore)" -- location can legitimately be "" (see
    models.Job), in which case the parenthesized clause is dropped rather
    than rendered empty."""
    where = f" ({_escape(location)})" if location else ""
    return f"{_escape(firm)} x{count}{where}"


def _escape(text: str, *, quote: bool = False) -> str:
    """Escape text for Telegram's HTML parse mode.

    `quote=True` for anything landing inside an href="..." attribute (a
    literal `"` in a URL would otherwise close the attribute early and
    corrupt the markup); plain body text never needs its quotes escaped.
    """
    return html.escape(text, quote=quote)


def _chunk(text: str, limit: int | None = None) -> list[str]:
    """Split `text` into <=`limit`-char pieces, never mid-line.

    Splitting on a blank line (the boundary between build_digest_text's
    header, per-category blocks, and trailing sheet link) first, falling
    back to individual lines only for a single block that's still too long
    on its own -- unlikely at this project's scale (see CLAUDE.md's Scale
    section) but a silent truncation mid-`<a href=...>` would send Telegram
    malformed HTML, which it either rejects outright or renders broken,
    either way worse than one extra message.

    A section's own first line (e.g. build_digest_text's "<b>SWE/DE</b>"
    category header) is carried forward into every continuation chunk that
    fallback produces for the rest of that section -- otherwise a category
    with enough (firm, location) groups to force a line-by-line split would
    hand out messages whose only mention of which category they belong to
    got flushed into an earlier chunk, exactly on the "busy day with many
    firms" this compact format is meant for.

    `limit` defaults to None (resolved to _MAX_MESSAGE_LEN inside the body)
    rather than defaulting the parameter directly to _MAX_MESSAGE_LEN --
    a default bound at def time would freeze the value used at import,
    silently ignoring a test (or future caller) that monkeypatches/changes
    the module constant afterward.
    """
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
    """Build and send the digest as one message (or a few, if it doesn't
    fit -- see _chunk), or do nothing at all.

    A no-op, logged at debug, when TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
    aren't configured or there's nothing new across all three categories.
    Never raises: a send failure is logged and swallowed rather than
    failing the whole scrape run over a notification (see module
    docstring) -- if the first chunk of a multi-message digest fails, the
    rest are skipped rather than sent out of order after a gap.

    `spreadsheet_url` is main.py's already-opened spreadsheet.url, passed
    straight through to build_digest_text for the trailing sheet link --
    this module never opens the spreadsheet itself.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("telegram digest: not configured, skipping")
        return

    text = build_digest_text(new_jobs_by_category, spreadsheet_url)
    if text is None:
        logger.info("telegram digest: nothing new to report, skipping")
        return

    _send_chunks(
        text, config.TELEGRAM_CHAT_ID, config.TELEGRAM_THREAD_ID, label="telegram digest"
    )


def build_new_companies_text(new_firms: dict[str, str], spreadsheet_url: str) -> str | None:
    """The new-company announcement, or None if no firm was added.

    `new_firms` maps each newly added firm to its company_type (see
    sheets.supported_companies.sync_supported_companies). Laid out like
    build_digest_text -- a bold company_type header per group, one firm per
    line under it, both sorted -- so _chunk's header carry-forward applies
    the same way on the (unlikely) day enough firms are added to need it.
    """
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
    link = f'<a href="{_escape(spreadsheet_url, quote=True)}">See their open roles in the sheet</a>'
    return "\n\n".join([header, *type_blocks, link])


def send_new_companies(new_firms: dict[str, str], spreadsheet_url: str) -> None:
    """Announce newly added firms to the TELEGRAM_COMPANIES_* topic, or do
    nothing at all.

    Same contract as send_digest: a no-op when TELEGRAM_BOT_TOKEN/
    TELEGRAM_COMPANIES_CHAT_ID aren't configured or `new_firms` is empty,
    and a send failure is logged, never raised.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_COMPANIES_CHAT_ID:
        logger.debug("telegram new companies: not configured, skipping")
        return

    text = build_new_companies_text(new_firms, spreadsheet_url)
    if text is None:
        logger.info("telegram new companies: no new firms, skipping")
        return

    _send_chunks(
        text,
        config.TELEGRAM_COMPANIES_CHAT_ID,
        config.TELEGRAM_COMPANIES_THREAD_ID,
        label="telegram new companies",
    )


def _send_chunks(text: str, chat_id: int, thread_id: int | None, *, label: str) -> None:
    """Send `text` as one message or a few (see _chunk), logging rather
    than raising on failure -- if one chunk fails, the rest are skipped
    rather than sent out of order after a gap."""
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
            return
    logger.info("%s: sent in %d message(s)", label, len(chunks))


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
    """Send a one-off test message, print the result, and return whether it
    worked -- a manual diagnostic for `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`,
    run via `uv run python -m jobscraper.telegram --check`. When
    `TELEGRAM_COMPANIES_CHAT_ID` is also set, that destination gets its own
    test message too, and both must succeed for this to return True.

    Deliberately the opposite of send_digest's behavior in both respects
    this differs from it: "not configured" and a send failure are both
    reported (printed and returned as False) rather than silently no-op'd
    or logged-and-swallowed -- the whole point of running this by hand is to
    surface exactly what's wrong, not hide it the way an automated run
    should.
    """
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
