"""Shared data shapes across job boards.

`Job` is the common shape every board's client must produce, so main.py and
the sheets writer never need to know which board a posting came from beyond
the `board` field itself. Adding a new board later means adding a new client
module that returns `list[Job]` -- nothing else in the project changes.
"""

from __future__ import annotations

import dataclasses
import datetime

# Keep in sync with sheets.writer.HEADER -- this is the column order written
# to, and read back from, the sheet. Ordered for a human scanning/filtering
# the sheet, not for how Job is constructed: the columns someone actually
# filters/sorts by (company, title, location, updated_at, company_type)
# come first; url still earns a near-front spot since it's the one column
# whose whole purpose is to be clicked; job_id and scraped_date are pure
# bookkeeping and sit last. Changing this order is a live-sheet migration,
# not just a code change -- see sheets/backfill.py, which rewrites every
# existing row (and the header) into whatever order FIELDS currently is.
FIELDS: tuple[str, ...] = (
    "board",
    "title",
    "location",
    "first_published",
    "updated_at",
    "company_type",
    "url",
    "seniority",
    "department",
    "job_id",
    "scraped_date",
)

DATE_FIELDS: tuple[str, ...] = (
    "first_published",
    "updated_at",
    "scraped_date",
)


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
      greenhouse_boards.toml's `firm` -- NOT the Greenhouse career-page slug
      used to fetch it. See job_boards/greenhouse/client.py's docstring for
      why, and the consequence for renaming a firm later.
      TODO(multi-board): once a second *type* of board exists, a company
      could in principle appear on two different board systems under the
      same token. Not a concern with Greenhouse only -- revisit (e.g. add a
      `source` field) when Lever/Workday support is added.
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
    company_type: from greenhouse_boards.toml's `company_type` (e.g. "Prop
      Trading/Hedge Fund") -- a fact about the firm, not something inferred
      from the title, so unlike seniority/board there's no classifier module
      for it: main.py reads it straight from config and passes it through
      job_boards/greenhouse/client.py. Defaults to "" for the same reason as
      seniority's default -- test Job(...) calls that don't care about it.
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

    def to_row(self) -> list[str]:
        """Render as a flat list of strings, in FIELDS order, for a sheet row."""
        return [
            self.board,
            self.title,
            self.location,
            normalize_date(self.first_published),
            normalize_date(self.updated_at),
            self.company_type,
            self.url,
            self.seniority,
            self.department,
            self.job_id,
            normalize_date(self.scraped_date),
        ]
