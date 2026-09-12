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
    "updated_at",
    "company_type",
    "url",
    "seniority",
    "department",
    "job_id",
    "scraped_date",
)


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
      NOT a reliable "posted date". See scraped_date for what pruning uses.
    scraped_date: the calendar date *this run* fetched the job. This -- not
      updated_at -- is what sheets.writer prunes on, because a board's own
      timestamps aren't a trustworthy signal of when a posting first
      appeared.
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

    def to_row(self) -> list[str]:
        """Render as a flat list of strings, in FIELDS order, for a sheet row."""
        return [
            self.board,
            self.title,
            self.location,
            self.updated_at,
            self.company_type,
            self.url,
            self.seniority,
            self.department,
            self.job_id,
            self.scraped_date.isoformat(),
        ]
