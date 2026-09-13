"""Optional Telegram digest of newly appended DA/BI, DS/ML/Algos, and SWE/DE
(PAC-only) postings, sent as one batched message at the end of a run.

Scoped to those three categories and to PAC (not QT/QR, not SRE/DevOps, not
RoW) on purpose -- see CLAUDE.md's Open work item this closes and
classify_region.py's docstring: the target audience is Singapore-based, and
those three categories are the ones it cares about a same-day nudge for.
main.py is what does that filtering (from append_jobs' now-returned newly
appended Job lists, keyed by tab_name(category, "PAC")) -- this module only
formats and sends whatever dict it's handed.

Deliberately a *count* digest, not a listing: one line per distinct
(category, firm, location) combination ("3 new SWE/DE roles from Grab
available in Singapore"), not one line per job with its own title and apply
link. Earlier versions of this digest put every new job's title and a direct
apply link straight in the message -- that gave everything away up front and
gave a reader no reason to ever open the tracker sheet itself. Grouping by
firm+location also collapses a firm posting several near-identical reqs into
one line instead of a wall of near-duplicate bullets. The message ends with
one link to the spreadsheet itself (not a per-job/per-category deep link) so
the reader goes there for titles, exact locations, and the real apply link.

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
"""

from __future__ import annotations

import html
import logging

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

    Within a category, jobs are grouped by (firm, location) -- see the
    module docstring for why a count line, not a per-job listing.
    `spreadsheet_url` is appended as one link at the end, only when there's
    actually something new to point at.
    """
    lines = []
    total = 0
    for category in DIGEST_CATEGORIES:
        jobs = new_jobs_by_category.get(category) or []
        if not jobs:
            continue
        total += len(jobs)
        lines.extend(_group_lines(category, jobs))

    if not lines:
        return None

    header = f"\U0001f4e6 {total} new PAC role(s)"
    link = f'<a href="{_escape(spreadsheet_url, quote=True)}">See them in the sheet</a>'
    return "\n\n".join([header, "\n".join(lines), link])


def _group_lines(category: str, jobs: list[Job]) -> list[str]:
    """One count line per distinct (firm, location) combo in `jobs`, in
    first-seen order."""
    counts: dict[tuple[str, str], int] = {}
    for job in jobs:
        key = (job.board, job.location)
        counts[key] = counts.get(key, 0) + 1
    return [
        _format_group_line(category, count, firm, location)
        for (firm, location), count in counts.items()
    ]


def _format_group_line(category: str, count: int, firm: str, location: str) -> str:
    """"3 new SWE/DE roles from Grab available in Singapore" -- location can
    legitimately be "" (see models.Job), in which case the "available in"
    clause is dropped rather than rendered empty."""
    role_word = "role" if count == 1 else "roles"
    where = f" available in {_escape(location)}" if location else ""
    return f"{count} new {_escape(category)} {role_word} from {_escape(firm)}{where}"


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
    header, grouped-count lines, and trailing sheet link) first, falling
    back to individual lines only for a single block that's still too long
    on its own -- unlikely at this project's scale (see CLAUDE.md's Scale
    section) but a silent truncation mid-`<a href=...>` would send Telegram
    malformed HTML, which it either rejects outright or renders broken,
    either way worse than one extra message.

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
        for line in section.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = line
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

    chunks = _chunk(text)
    for index, chunk in enumerate(chunks, start=1):
        try:
            _send_message(chunk)
        except Exception:
            logger.exception(
                "telegram digest: failed to send message %d/%d, skipping the rest",
                index,
                len(chunks),
            )
            return
    logger.info("telegram digest: sent in %d message(s)", len(chunks))


def _send_message(text: str) -> None:
    url = _SEND_MESSAGE_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if config.TELEGRAM_THREAD_ID is not None:
        payload["message_thread_id"] = config.TELEGRAM_THREAD_ID
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
