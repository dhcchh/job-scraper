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

# Greenhouse board tokens live in a checked-in TOML file, not an env var --
# they aren't secret (just company career-page slugs), and a growing list of
# many firms is much easier to read/edit as a TOML array-of-tables than as
# one long comma-separated string. See greenhouse_boards.toml itself.
_BOARDS_FILE = Path(__file__).resolve().parents[2] / "greenhouse_boards.toml"


def _load_boards(path: Path) -> list[dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)["boards"]


GREENHOUSE_BOARDS: list[dict] = _load_boards(_BOARDS_FILE)

# The spreadsheet id from its URL:
# https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit
#
# Tab names aren't configured here -- sheets.writer.get_worksheet() opens one
# tab per jobscraper.enrichment.classify_roles.CATEGORIES entry, by
# that category's name.
GOOGLE_SHEET_ID: str = os.environ["GOOGLE_SHEET_ID"]

# The full service account JSON key, as one raw string -- not a file path.
# This is what lets a GitHub Actions secret carry it without a "write secret
# to a temp file" workflow step. Parsed with json.loads() in sheets/writer.py.
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
