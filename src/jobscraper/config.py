"""Load environment variables and job-board configuration."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PACKAGE_BOARD_DIR = Path(__file__).resolve().parent / "company_toml"
_COMPANY_TOML_DIR = (
    _PACKAGE_BOARD_DIR
    if _PACKAGE_BOARD_DIR.is_dir()
    else Path(__file__).resolve().parents[2] / "company_toml"
)
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


def _boards(name: str) -> list[dict]:
    return _load_boards(_COMPANY_TOML_DIR / f"{name}_boards.toml")


GREENHOUSE_BOARDS = _boards("greenhouse")
LEVER_BOARDS = _boards("lever")
ASHBY_BOARDS = _boards("ashby")
SMARTRECRUITERS_BOARDS = _boards("smartrecruiters")
EIGHTFOLD_BOARDS = _boards("eightfold")
WORKDAY_BOARDS = _boards("workday")
RECRUITEE_BOARDS = _boards("recruitee")

GOOGLE_SHEET_ID: str = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SHEET_TITLE: str | None = os.environ.get("GOOGLE_SHEET_TITLE") or None
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]


def _optional_int(name: str) -> int | None:
    """Read an optional integer, treating a blank secret as unset."""
    val = os.environ.get(name)
    return int(val) if val else None


TELEGRAM_BOT_TOKEN: str | None = os.environ.get("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID: int | None = _optional_int("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID: int | None = _optional_int("TELEGRAM_THREAD_ID")

TELEGRAM_COMPANIES_CHAT_ID: int | None = _optional_int("TELEGRAM_COMPANIES_CHAT_ID")
TELEGRAM_COMPANIES_THREAD_ID: int | None = _optional_int("TELEGRAM_COMPANIES_THREAD_ID")


def _optional_set(name: str) -> frozenset[str]:
    """Read an optional comma-separated list, ignoring blank entries."""
    items = os.environ.get(name, "").split(",")
    return frozenset(item.strip() for item in items if item.strip())


# Firms whose prune guard (sheets.writer.held_back_prunes) is bypassed for
# this run, after confirming their closures are real.
FORCE_PRUNE_FIRMS: frozenset[str] = _optional_set("FORCE_PRUNE_FIRMS")

# When non-empty, only fetch and append jobs for these firms (all others are
# skipped). Used by the daily-scrape workflow on TOML pushes to run only the
# newly added firms rather than the full board list.
ONLY_FIRMS: frozenset[str] = _optional_set("ONLY_FIRMS")
