"""Every environment variable (and the boards config file) is read here, once.

Read values through the module (`config.GOOGLE_SHEET_ID`), not by copying
them at import elsewhere, so tests can monkeypatch one value and repoint the
whole job at test data.
"""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Greenhouse/Lever board tokens live in checked-in TOML files, not an env
# var -- they aren't secret (just company career-page slugs), and a growing
# list of many firms is much easier to read/edit as a TOML array-of-tables
# than as one long comma-separated string. Grouped under company_toml/
# (rather than the repo root) so the root isn't cluttered as more board
# platforms are added -- see company_toml/greenhouse_boards.toml,
# company_toml/lever_boards.toml, company_toml/ashby_boards.toml,
# company_toml/smartrecruiters_boards.toml, company_toml/eightfold_boards.toml,
# and company_toml/workday_boards.toml themselves.
_COMPANY_TOML_DIR = Path(__file__).resolve().parents[2] / "company_toml"
_BOARDS_FILE = _COMPANY_TOML_DIR / "greenhouse_boards.toml"
_LEVER_BOARDS_FILE = _COMPANY_TOML_DIR / "lever_boards.toml"
_ASHBY_BOARDS_FILE = _COMPANY_TOML_DIR / "ashby_boards.toml"
_SMARTRECRUITERS_BOARDS_FILE = _COMPANY_TOML_DIR / "smartrecruiters_boards.toml"
_EIGHTFOLD_BOARDS_FILE = _COMPANY_TOML_DIR / "eightfold_boards.toml"
_WORKDAY_BOARDS_FILE = _COMPANY_TOML_DIR / "workday_boards.toml"


_REQUIRED_BOARD_FIELDS = ("token", "firm", "company_type")


def _load_boards(path: Path) -> list[dict]:
    with open(path, "rb") as f:
        boards = tomllib.load(f)["boards"]
    for index, board in enumerate(boards, start=1):
        missing = [field for field in _REQUIRED_BOARD_FIELDS if not board.get(field)]
        if missing:
            identifier = board.get("firm") or board.get("token") or f"#{index}"
            raise ValueError(
                f"{path.name}: [[boards]] entry {identifier!r} is missing "
                f"required field(s): {', '.join(missing)}"
            )
    return boards


GREENHOUSE_BOARDS: list[dict] = _load_boards(_BOARDS_FILE)

# Same required fields (token/firm/company_type), same validation -- Lever's
# `token` is its own site slug rather than a Greenhouse board token, but
# _load_boards doesn't care which board type a file is for.
LEVER_BOARDS: list[dict] = _load_boards(_LEVER_BOARDS_FILE)

# Same shape again -- Ashby's `token` is its job-board slug
# (jobs.ashbyhq.com/<token>), same idea as Lever's site slug.
ASHBY_BOARDS: list[dict] = _load_boards(_ASHBY_BOARDS_FILE)

# Same shape again -- SmartRecruiters' `token` is its company identifier
# (jobs.smartrecruiters.com/<token>), same idea as Lever's/Ashby's slug.
SMARTRECRUITERS_BOARDS: list[dict] = _load_boards(_SMARTRECRUITERS_BOARDS_FILE)

# Same shape again -- Eightfold's `token` is a company's own Eightfold
# subdomain (<token>.eightfold.ai), same idea as Ashby's/Lever's slug.
EIGHTFOLD_BOARDS: list[dict] = _load_boards(_EIGHTFOLD_BOARDS_FILE)

# Same shape again -- Workday's `token` is "<tenant>.<wdN>/<site>", the three
# pieces of a company's careers-site URL (<tenant>.<wdN>.myworkdayjobs.com/
# <site>) that all vary per company -- see job_boards/workday/client.py.
WORKDAY_BOARDS: list[dict] = _load_boards(_WORKDAY_BOARDS_FILE)

# The spreadsheet id from its URL:
# https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit
#
# Tab names aren't configured here -- sheets.writer.get_worksheet() opens one
# tab per jobscraper.enrichment.classify_roles.CATEGORIES entry, by
# that category's name.
GOOGLE_SHEET_ID: str = os.environ["GOOGLE_SHEET_ID"]

# Optional: the spreadsheet's exact display title. GOOGLE_SHEET_ID is set
# independently here (.env) and in the GitHub Actions secret, with nothing
# else cross-checking the two -- when this is set, sheets.writer.
# get_spreadsheet() raises if the opened spreadsheet's title doesn't match,
# catching that drift before anything is written instead of only logging it.
GOOGLE_SHEET_TITLE: str | None = os.environ.get("GOOGLE_SHEET_TITLE") or None

# The full service account JSON key, as one raw string -- not a file path.
# This is what lets a GitHub Actions secret carry it without a "write secret
# to a temp file" workflow step. Parsed with json.loads() in sheets/writer.py.
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]


def _optional_int(name: str) -> int | None:
    """An optional integer env var; None when unset/blank.

    A GitHub Actions secret that doesn't exist still shows up as an empty
    string in the workflow's `env:` block (not simply absent), so this
    treats "" the same as unset rather than raising on int("").
    """
    val = os.environ.get(name)
    return int(val) if val else None


# Optional Telegram digest of newly appended DA/BI, DS/ML/Algos, and SWE/DE
# postings (PAC region only, matching this project's Singapore-based
# audience -- see classify_region.py) -- see telegram.py. Unset (the
# default for all three) disables the feature entirely rather than raising,
# the same convention data-career-hustler-bot's own JOBS_CHAT_ID/
# RESOURCES_CHAT_ID use for an optional destination.
#
# TELEGRAM_BOT_TOKEN can reuse the exact @BotFather bot
# data-career-hustler-bot already runs (a bot token isn't tied to one chat),
# but TELEGRAM_CHAT_ID/TELEGRAM_THREAD_ID should point at a DIFFERENT topic
# from that bot's own JOBS_CHAT_ID/JOBS_THREAD_ID -- these are unreviewed
# scraped postings, not the same thing as that bot's member-curated,
# point-earning /postjob listings, and mixing the two would make the manual
# topic unreadable. TELEGRAM_THREAD_ID is optional even when the rest is
# set (a non-forum group or a DM has no topics).
TELEGRAM_BOT_TOKEN: str | None = os.environ.get("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID: int | None = _optional_int("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID: int | None = _optional_int("TELEGRAM_THREAD_ID")
