"""Shared data shapes across job boards.

`Job` is the common shape every board's client must produce, so main.py and
the sheets writer never need to know which board a posting came from beyond
the `board` field itself. Adding a new board later means adding a new client
module that returns `list[Job]` -- nothing else in the project changes.
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import re

# Keep in sync with sheets.writer.HEADER -- this is the column order written
# to, and read back from, the sheet. Ordered for a human scanning/filtering
# the sheet, not for how Job is constructed: first_published/updated_at lead
# so the "posted in the last 7 days" conditional-formatting rule (see
# CLAUDE.md) anchors on column A; board/title/location/seniority/
# company_type -- the columns someone actually filters/sorts by, and what
# sheets.writer.set_basic_filter's dropdown arrows target -- follow; url
# still earns a spot ahead of the rest since it's the one column whose whole
# purpose is to be clicked; department, job_id, scraped_date, and source are
# comparatively bookkeeping and trail. Changing this order is a live-sheet
# migration, not just a code change -- see sheets/backfill.py, which
# rewrites every existing row (and the header) into whatever order FIELDS
# currently is, including re-pointing format_date_columns and each tab's
# conditional-formatting rule at wherever the date columns land now (see
# CLAUDE.md).
# `description` is appended after everything above, including `source` --
# same reasoning as `source`'s own placement (see git history/CLAUDE.md): it
# doesn't belong with the columns someone filters/sorts by (it's freeform,
# multi-paragraph JD text, not a category), and appending rather than
# inserting means every column before it keeps its current letter, so
# format_date_columns/set_basic_filter and each tab's conditional-formatting
# rule (see CLAUDE.md) don't need to move.
FIELDS: tuple[str, ...] = (
    "first_published",
    "updated_at",
    "board",
    "title",
    "location",
    "seniority",
    "company_type",
    "url",
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

# `board` values (see Job.board) for firms confirmed to have an apply page
# that doesn't reliably show its own JD -- Qube's Greenhouse page only ever
# renders the JD into a client-side hydration blob (invisible/unsearchable
# on the page), and Squarepoint's `absolute_url` redirects to a generic
# careers listing rather than the specific posting. `description` is only
# ever populated for a firm in this set -- see each job_boards/*/client.py's
# `_to_job` -- rather than for every posting from every board: this is a
# deliberate trial scoped to the two known-broken firms, not a blanket
# rollout, since most employers' own apply pages already show the JD fine
# and duplicating several-KB of text into every other row's cell for no
# reason works against Sheets' cell cap (see CLAUDE.md's Scale section) for
# no benefit. Expand this set if another firm turns out to have the same
# problem -- sheets/backfill.py blanks `description` back to "" for any
# firm that's since been removed from it, so narrowing this set cleans up
# the sheet on the next backfill too, not just widening it.
FIRMS_NEEDING_DESCRIPTION: frozenset[str] = frozenset(
    {"Qube Research & Technologies", "Squarepoint"}
)

# The display text every job posting link uses instead of its raw url --
# see hyperlink_formula. One constant rather than per-Job text since the
# point is a short, uniform label a human can scan a column of, not a
# description of each specific posting (the title/board columns already
# say what the job is).
LINK_LABEL = "Apply"


def hyperlink_formula(url: str, label: str = LINK_LABEL) -> str:
    """A Sheets `=HYPERLINK(url, label)` formula, or "" if url is empty.

    Every writer in this project uses value_input_option="USER_ENTERED",
    which parses a leading "=" as a formula -- so the cell still renders as
    a real, clickable link, just showing `label` instead of the full raw
    url. Returns "" for an empty url rather than a formula with nothing to
    link to, matching how a plain empty url cell has always rendered here.

    A literal `"` in either argument is escaped by doubling it, the way a
    Sheets formula string literal escapes an embedded quote.
    """
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
    """Best-effort HTML -> plain text, for a board's raw JD markup.

    Greenhouse's `content` field is HTML-entity-double-encoded (its own API
    quirk, confirmed across every board this project fetches: the real
    markup is itself escaped, e.g. a literal `&lt;p&gt;` in the JSON value
    rather than `<p>`) -- unescaping twice, once before stripping tags and
    once after, undoes that and also decodes any real entity (`&amp;`) the
    underlying content has. A `<li>` becomes a "- " bullet line and
    `<p>`/`<div>`/heading/`<ul>`/`<ol>` closes and `<br>` become newlines
    before every remaining tag is dropped, so list/paragraph structure
    survives as plain-text line breaks instead of being squashed into one
    run-on line. Collapses to "" for empty/missing input, matching every
    other string field's empty-default here rather than raising.
    """
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
    """Normalize any date/datetime/string into an ISO date string (YYYY-MM-DD), or "".

    Returns "" for empty/missing values. For ISO 8601 strings (e.g.
    "2026-09-09T05:54:53-04:00" or "2026-06-01T00:00:00Z"), extracts the
    calendar date part "YYYY-MM-DD" so Google Sheets USER_ENTERED recognizes
    it as a true date value rather than a plain string.
    """
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
    """One job posting, normalized across job boards.

    board: the firm's human-readable name (e.g. "Jane Street"), from
      company_toml/greenhouse_boards.toml's `firm` -- NOT the Greenhouse
      career-page slug used to fetch it. See
      job_boards/greenhouse/client.py's docstring for why, and the
      consequence for renaming a firm later. A firm could in principle run
      boards on more than one board system at once (e.g. Greenhouse for one
      team, Lever for another) under this same name -- `source` (below) is
      what distinguishes those, not this field.
    job_id: the board's own id for the posting, as a string. Greenhouse's
      ids are integers; keeping this as `str` means a board with
      non-numeric ids doesn't need a model change.
    updated_at: the board's own "last updated" timestamp, best-effort only --
      NOT a reliable "posted date" (an edit bumps it same as a new posting
      would). See first_published for Greenhouse's real "went live"
      timestamp, and scraped_date for what pruning uses.
    first_published: Greenhouse's own "job went live" timestamp, straight
      from the Job Board API's `first_published` field (documented in
      Greenhouse's own API examples, though not described in prose there).
      Unlike updated_at, confirmed empirically -- across ~5,000 real
      postings on this project's boards -- to never move once set, even
      across later edits: this is the best "when did this posting actually
      first appear" signal available. Best-effort still, same as every
      other Greenhouse-sourced string field here: a small fraction of real
      postings (observed on Squarepoint/Tower Research/HRT) have it
      missing, so this defaults to "" rather than raising.
    scraped_date: the calendar date *this run* fetched the job. This -- not
      updated_at or first_published -- is what sheets.writer prunes on,
      because it's this project's own signal (never absent, never revised
      by the board) of whether a posting is still being seen at all.
    seniority: one of jobscraper.seniority.LEVELS, or "Unspecified" if the
      title carries no seniority wording at all -- see that module for why
      "Unspecified" is a real, common outcome rather than a fallback bug.
      Defaults to "Unspecified" so every other Job(...) call site in tests
      doesn't need to pass it explicitly.
    company_type: from company_toml/greenhouse_boards.toml's `company_type`
      (e.g. "Prop Trading/Hedge Fund") -- a fact about the firm, not
      something inferred from the title, so unlike seniority/board there's
      no classifier module for it: main.py reads it straight from config and
      passes it through job_boards/greenhouse/client.py. Defaults to "" for
      the same reason as seniority's default -- test Job(...) calls that
      don't care about it.
    source: which board system this posting was fetched from -- "Greenhouse",
      "Lever", or "Ashby" -- a fact about which client fetched it, not
      something in the API response itself, so each job_boards/*/client.py
      stamps it as a fixed literal in its own `_to_job` rather than reading
      it from `raw`. Defaults to "" for the same reason as company_type's
      default -- test Job(...) calls that don't care about it.
    description: the full JD text, but ONLY for a firm in
      FIRMS_NEEDING_DESCRIPTION -- "" for every other posting, even though
      every client could technically assemble one. Board-specific in how
      it's assembled where it is populated: Greenhouse's client runs
      strip_html() over the already-fetched `content=true` HTML; Lever's
      and Ashby's APIs hand back ready-made plain text
      (`descriptionBodyPlain`/`lists`/`descriptionPlain`), so their clients
      use that directly instead of re-deriving it from HTML. Exists because
      an employer's own apply page can't be trusted to show the JD at all --
      see FIRMS_NEEDING_DESCRIPTION's comment for the confirmed cases -- so
      the sheet itself needs to be a reliable place to actually read the JD
      for those specific firms. Defaults to "" for the same reason as
      company_type's default -- test Job(...) calls that don't care about
      it.
    """

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

    def to_row(self) -> list[str]:
        """Render as a flat list of strings, in FIELDS order, for a sheet row.

        The url column is wrapped in hyperlink_formula() rather than written
        as a raw url: Sheets auto-linkifies a plain url on USER_ENTERED
        already, but shows the whole url as the cell's visible text, which
        is unreadable once the column is more than a few characters wide.
        `self.url` itself stays the raw url so anything else on this Job
        (e.g. a future Telegram digest) still has the real link.
        """
        return [
            normalize_date(self.first_published),
            normalize_date(self.updated_at),
            self.board,
            self.title,
            self.location,
            self.seniority,
            self.company_type,
            hyperlink_formula(self.url),
            self.department,
            self.job_id,
            normalize_date(self.scraped_date),
            self.source,
            self.description,
        ]
