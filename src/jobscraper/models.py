"""Shared job data and sheet-value helpers."""

import dataclasses
import datetime
import html
import re

# Column order is a live sheet schema; changes require a full refresh.
FIELDS: tuple[str, ...] = (
    "first_published",
    "updated_at",
    "board",
    "title",
    "location",
    "seniority",
    "company_type",
    "url",
    "salary",
    "department",
    "job_id",
    "scraped_date",
    "source",
    "description",
)

DATE_FIELDS: tuple[str, ...] = (
    "first_published",
    "updated_at",
    "scraped_date",
)

FIRMS_NEEDING_DESCRIPTION: frozenset[str] = frozenset({"QRT", "Squarepoint"})

LINK_LABEL = "Apply"


def hyperlink_formula(url: str, label: str = LINK_LABEL) -> str:
    """Return a Sheets hyperlink formula, escaping formula string literals."""
    if not url:
        return ""
    escaped_url = url.replace('"', '""')
    escaped_label = label.replace('"', '""')
    return f'=HYPERLINK("{escaped_url}", "{escaped_label}")'


_HTML_LIST_ITEM_RE = re.compile(r"(?i)<li[^>]*>")
_HTML_BLOCK_CLOSE_RE = re.compile(r"(?i)</(p|div|h[1-6]|ul|ol)\s*>")
_HTML_BR_RE = re.compile(r"(?i)<br\s*/?>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")
_BLANK_LINE_RUN_RE = re.compile(r"\n{3,}")


def strip_html(raw: str) -> str:
    """Convert board-provided HTML, including double-encoded markup, to text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _HTML_LIST_ITEM_RE.sub("\n- ", text)
    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_BLOCK_CLOSE_RE.sub("\n\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WHITESPACE_RUN_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINE_RUN_RE.sub("\n\n", text)
    return text.strip()


def normalize_date(value: str | datetime.date | datetime.datetime | None) -> str:
    """Normalize dates and ISO timestamps to `YYYY-MM-DD`."""
    if not value:
        return ""
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return ""
        if len(val) == 10 and val[4] == "-" and val[7] == "-":
            try:
                return datetime.date.fromisoformat(val).isoformat()
            except ValueError:
                pass
        try:
            return datetime.datetime.fromisoformat(val).date().isoformat()
        except ValueError:
            pass
        if len(val) >= 10 and val[4] == "-" and val[7] == "-":
            try:
                return datetime.date.fromisoformat(val[:10]).isoformat()
            except ValueError:
                pass
        return val
    return str(value)


@dataclasses.dataclass(frozen=True, slots=True)
class Job:
    """A job posting normalized across supported board providers."""

    board: str
    job_id: str
    title: str
    department: str
    location: str
    url: str
    updated_at: str
    scraped_date: datetime.date
    seniority: str = "Unspecified"
    company_type: str = ""
    first_published: str = ""
    source: str = ""
    description: str = ""
    salary: str | None = None

    def to_row(self) -> list[str]:
        """Render the job in `FIELDS` order for Google Sheets."""
        return [
            normalize_date(self.first_published),
            normalize_date(self.updated_at),
            self.board,
            self.title,
            self.location,
            self.seniority,
            self.company_type,
            hyperlink_formula(self.url),
            self.salary or "",
            self.department,
            self.job_id,
            normalize_date(self.scraped_date),
            self.source,
            self.description,
        ]
