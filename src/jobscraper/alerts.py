"""DM the operator each new Singapore/Hong Kong data-engineering role.

Separate from telegram.py's group digest: one message per posting, with its
apply link and full job description, to TELEGRAM_DM_CHAT_ID. Disabled unless
that and TELEGRAM_BOT_TOKEN are set, and never raises.
"""

import html
import logging
import re
import textwrap
import time
from urllib.parse import urlparse

from jobscraper import config
from jobscraper.enrichment.classify_region import office_countries
from jobscraper.enrichment.classify_roles import _NOT_AN_ENGINEERING_ROLE_RE
from jobscraper.job_boards._http import get_with_retries
from jobscraper.job_boards.smartrecruiters.client import BASE_URL as SMARTRECRUITERS_API
from jobscraper.models import Job, strip_html
from jobscraper.telegram import send_chunks

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
_NOT_DATA_ENGINEERING_RE = re.compile(
    r"\brecruit|\bdata cent(?:er|re)\b|\bsolutions architect|\blegal engineer",
    re.IGNORECASE,
)

MAX_ALERTS_PER_RUN = 15
_MAX_JD_CHARS = 10_000
_MAX_LINE_CHARS = 3_000
_PAUSE_SECONDS = 1.0  # Telegram allows about one message per second per chat


def is_alert_match(job: Job) -> bool:
    """Whether a posting is a data-engineering role with a SG/HK office."""
    return _is_data_engineering_title(job.title) and any(
        country in ALERT_COUNTRIES for country in office_countries(job.location)
    )


def _is_data_engineering_title(title: str) -> bool:
    if _NOT_AN_ENGINEERING_ROLE_RE.search(title) or _NOT_DATA_ENGINEERING_RE.search(title):
        return False
    if _DE_TITLE_RE.search(title):
        return True
    return bool(_DATA_RE.search(title) and _ENGINEER_RE.search(title))


def fetch_jd(job: Job) -> str:
    """Return the posting's plain-text JD, or "" when unavailable."""
    if job.jd_raw:
        return strip_html(job.jd_raw)
    try:
        if job.source == "Workday":
            return _workday_jd(job)
        if job.source == "SmartRecruiters":
            return _smartrecruiters_jd(job)
    except Exception:
        logger.exception("alerts: failed to fetch JD for %s %s", job.board, job.job_id)
    return ""


def _workday_jd(job: Job) -> str:
    """Read a Workday posting's detail endpoint, derived from its page URL."""
    parsed = urlparse(job.url)
    site, _, external_path = parsed.path.lstrip("/").partition("/")
    tenant = parsed.netloc.split(".", 1)[0]
    url = f"https://{parsed.netloc}/wday/cxs/{tenant}/{site}/{external_path}"
    info = get_with_retries("workday", tenant, url).json().get("jobPostingInfo") or {}
    return strip_html(info.get("jobDescription") or "")


def _smartrecruiters_jd(job: Job) -> str:
    """Join the text sections of a SmartRecruiters posting's detail."""
    identifier, _, posting_id = urlparse(job.url).path.strip("/").partition("/")
    url = f"{SMARTRECRUITERS_API}/{identifier}/postings/{posting_id}"
    sections = (
        (get_with_retries("smartrecruiters", identifier, url).json().get("jobAd") or {})
        .get("sections")
        or {}
    )
    parts = []
    for section in sections.values():
        text = strip_html((section or {}).get("text") or "")
        if text:
            title = (section.get("title") or "").strip()
            parts.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(parts)


def build_alert_text(job: Job, jd: str) -> str:
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
    return f"{header}\n\n<b>Job description</b>\n{_format_jd(jd)}"


def _format_jd(jd: str) -> str:
    if not jd:
        return "Not available from this board, see the link."
    if len(jd) > _MAX_JD_CHARS:
        jd = jd[:_MAX_JD_CHARS].rstrip() + "\n… (truncated, see the link)"
    # telegram._chunk only splits at line breaks, so no line may exceed a message.
    lines = []
    for line in jd.split("\n"):
        lines.extend(textwrap.wrap(line, _MAX_LINE_CHARS) or [""])
    return _escape("\n".join(lines))


def _escape(text: str, *, quote: bool = False) -> str:
    return html.escape(text, quote=quote)


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
            text = build_alert_text(job, fetch_jd(job))
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
