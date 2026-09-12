"""Shared fixtures.

config reads required env vars at import and raises without them, so
sensible defaults are set here before jobscraper.config is ever imported --
conftest runs first, and setdefault leaves a real .env alone.
"""

import os

os.environ.setdefault("GREENHOUSE_BOARD_TOKENS", "test-board")
os.environ.setdefault("GOOGLE_SHEET_ID", "test-sheet-id")
os.environ.setdefault(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    '{"type": "service_account", "project_id": "test", '
    '"private_key": "x", "client_email": "test@example.com"}',
)
