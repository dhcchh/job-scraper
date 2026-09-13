"""Shared fixtures.

config reads required env vars at import and raises without them, so
sensible defaults are set here before jobscraper.config is ever imported --
conftest runs first, and setdefault leaves a real .env alone. Greenhouse
board tokens come from company_toml/greenhouse_boards.toml (checked in,
always present) rather than an env var, so no default is needed for those
here.
"""

import os

os.environ.setdefault("GOOGLE_SHEET_ID", "test-sheet-id")
os.environ.setdefault(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    '{"type": "service_account", "project_id": "test", '
    '"private_key": "x", "client_email": "test@example.com"}',
)
