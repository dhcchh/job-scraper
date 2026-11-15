"""Read-only data quality audit; exits nonzero for broken keys or schemas."""

import argparse
import datetime
import json
from pathlib import Path

from jobscraper.cli import write_step_summary
from jobscraper.quality import inspect_tabs, summary_markdown
from jobscraper.routing import JOB_TABS
from jobscraper.sheets.writer import get_spreadsheet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data-quality.json")
    args = parser.parse_args()
    sheet = get_spreadsheet(read_only=True)
    expected = list(JOB_TABS)
    tabs = {ws.title: ws.get_all_values() for ws in sheet.worksheets() if ws.title in expected}
    report = inspect_tabs(tabs, expected)
    report["audited_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["spreadsheet_title"] = sheet.title
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    markdown = summary_markdown(report)
    print(markdown)
    write_step_summary(markdown)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
