"""Load environment variables and job-board configuration."""

import os

from dotenv import load_dotenv

from jobscraper.board_config import (
    DEFAULT_BOARD_DIR,
    PROVIDER_LABELS,
    PROVIDERS,
    load_all_boards,
)

load_dotenv()


_BOARDS = load_all_boards(DEFAULT_BOARD_DIR)


GREENHOUSE_BOARDS = _BOARDS["greenhouse"]
LEVER_BOARDS = _BOARDS["lever"]
ASHBY_BOARDS = _BOARDS["ashby"]
SMARTRECRUITERS_BOARDS = _BOARDS["smartrecruiters"]
EIGHTFOLD_BOARDS = _BOARDS["eightfold"]
WORKDAY_BOARDS = _BOARDS["workday"]
RECRUITEE_BOARDS = _BOARDS["recruitee"]
CITADEL_BOARDS = _BOARDS["citadel"]
AMAZON_BOARDS = _BOARDS["amazon"]


def board_sources() -> list[tuple[str, str, list[dict]]]:
    """Read the configured provider lists, including runtime overrides."""
    return [
        (name, PROVIDER_LABELS[name], globals()[f"{name.upper()}_BOARDS"])
        for name in PROVIDERS
    ]


def require_sheet_credentials() -> None:
    """Validate sheet settings only for commands that actually open a sheet."""
    missing = [
        name for name in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON")
        if not globals()[name]
    ]
    if missing:
        raise ValueError("Missing required sheet settings: " + ", ".join(missing))


GOOGLE_SHEET_ID: str = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SHEET_TITLE: str | None = os.environ.get("GOOGLE_SHEET_TITLE") or None
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")


def _optional_int(name: str) -> int | None:
    """Read an optional integer, treating a blank secret as unset."""
    val = os.environ.get(name)
    return int(val) if val else None


TELEGRAM_BOT_TOKEN: str | None = os.environ.get("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID: int | None = _optional_int("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID: int | None = _optional_int("TELEGRAM_THREAD_ID")

TELEGRAM_COMPANIES_CHAT_ID: int | None = _optional_int("TELEGRAM_COMPANIES_CHAT_ID")
TELEGRAM_COMPANIES_THREAD_ID: int | None = _optional_int("TELEGRAM_COMPANIES_THREAD_ID")

# A private chat with the operator (their numeric user id) for alerts.py's
# per-role DMs; the user must have pressed Start on the bot first.
TELEGRAM_DM_CHAT_ID: int | None = _optional_int("TELEGRAM_DM_CHAT_ID")


def _optional_set(name: str) -> frozenset[str]:
    """Read an optional comma-separated list, ignoring blank entries."""
    items = os.environ.get(name, "").split(",")
    return frozenset(item.strip() for item in items if item.strip())


# Firms whose prune guard (sheets.writer.held_back_prunes) is bypassed for
# this run, after confirming their closures are real.
FORCE_PRUNE_FIRMS: frozenset[str] = _optional_set("FORCE_PRUNE_FIRMS")

# When non-empty, only fetch and append jobs for these firms (all others are
# skipped). Used by the daily-scrape workflow on TOML pushes to run only the
# firms with added or modified board entries.
ONLY_FIRMS: frozenset[str] = _optional_set("ONLY_FIRMS")
