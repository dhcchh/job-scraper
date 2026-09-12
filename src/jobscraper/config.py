"""Every environment variable is read here, once.

Read values through the module (`config.GOOGLE_SHEET_ID`), not by copying
them at import elsewhere, so tests can monkeypatch one value and repoint the
whole job at test data.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Comma-separated Greenhouse board tokens, e.g. "stripe,airbnb,figma" -- the
# slug in a company's Greenhouse careers URL (boards.greenhouse.io/<token>),
# not an API key. See .env.example for how to find one.
_raw_tokens = os.environ["GREENHOUSE_BOARD_TOKENS"]
GREENHOUSE_BOARD_TOKENS: list[str] = [
    token.strip() for token in _raw_tokens.split(",") if token.strip()
]

# The spreadsheet id from its URL:
# https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit
GOOGLE_SHEET_ID: str = os.environ["GOOGLE_SHEET_ID"]

# Tab name to write to.
# TODO(extension point): sheets.writer.get_worksheet() assumes this tab
# already exists; add .add_worksheet(...) there if you want it created
# automatically.
GOOGLE_SHEET_WORKSHEET_NAME: str = os.getenv("GOOGLE_SHEET_WORKSHEET_NAME", "Jobs")

# The full service account JSON key, as one raw string -- not a file path.
# This is what lets a GitHub Actions secret carry it without a "write secret
# to a temp file" workflow step. Parsed with json.loads() in sheets/writer.py.
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

# A run prunes any sheet row whose scraped_date is older than today minus
# this many days.
PRUNE_AFTER_DAYS: int = int(os.getenv("PRUNE_AFTER_DAYS", "60"))
