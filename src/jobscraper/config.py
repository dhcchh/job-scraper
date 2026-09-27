"""Load environment variables and job-board configuration."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_COMPANY_TOML_DIR = Path(__file__).resolve().parents[2] / "company_toml"
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
