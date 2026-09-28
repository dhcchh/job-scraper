"""Read-only data quality audit; exits nonzero for broken keys or schemas."""

import argparse
import datetime
import json
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from jobscraper.enrichment.classify_region import REGIONS, tab_name
from jobscraper.enrichment.classify_roles import CATEGORIES
from jobscraper.quality import inspect_tabs, summary_markdown


def main() -> None:
    from jobscraper import config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data-quality.json")
    args = parser.parse_args()
    credentials = Credentials.from_service_account_info(
        json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    sheet = gspread.authorize(credentials, http_client=gspread.BackOffHTTPClient).open_by_key(config.GOOGLE_SHEET_ID)
    if config.GOOGLE_SHEET_TITLE and sheet.title != config.GOOGLE_SHEET_TITLE:
        raise RuntimeError("Configured spreadsheet title does not match")
    expected = [tab_name(category, region) for category, _ in CATEGORIES for region in REGIONS]
    tabs = {ws.title: ws.get_all_values() for ws in sheet.worksheets() if ws.title in expected}
    report = inspect_tabs(tabs, expected)
    report["audited_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["spreadsheet_title"] = sheet.title
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    markdown = summary_markdown(report)
    print(markdown)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as summary:
            summary.write(markdown)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
