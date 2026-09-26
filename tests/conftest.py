"""Shared fixtures.

config reads required env vars at import and raises without them, so
sensible defaults are set here before jobscraper.config is ever imported --
conftest runs first, and setdefault leaves a real .env alone. Greenhouse
board tokens come from company_toml/greenhouse_boards.toml (checked in,
always present) rather than an env var, so no default is needed for those
here.

TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID/TELEGRAM_THREAD_ID (and
TELEGRAM_COMPANIES_CHAT_ID/TELEGRAM_COMPANIES_THREAD_ID, the new-company
announcement's destination) are forced to "" the same way, for a different reason: they're optional, so config.py doesn't
require them to be set at all, but a real `.env` configured for the actual
production digest (as this repo's is) would otherwise flow straight through
python-dotenv's load_dotenv() (called at jobscraper.config import time, with
its default override=False -- it never overwrites a key already in
os.environ) into every test that calls main.run() or telegram.send_digest
without itself monkeypatching config.TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.
Without this, send_digest's "unconfigured -> no-op" guard silently sees real
credentials and posts each such test's fake job data (e.g. "Acme", "GoodCo")
to the real Telegram chat -- confirmed actually happening, not theoretical.
"""

import os

os.environ.setdefault("GOOGLE_SHEET_ID", "test-sheet-id")
os.environ.setdefault(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    '{"type": "service_account", "project_id": "test", '
    '"private_key": "x", "client_email": "test@example.com"}',
)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")
os.environ.setdefault("TELEGRAM_THREAD_ID", "")
os.environ.setdefault("TELEGRAM_COMPANIES_CHAT_ID", "")
os.environ.setdefault("TELEGRAM_COMPANIES_THREAD_ID", "")
