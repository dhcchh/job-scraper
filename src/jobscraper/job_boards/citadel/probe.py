"""Fetch Citadel without sheet access and report the production activation gate."""

import argparse
import datetime
import json
import logging
import os
import time
from pathlib import Path

from jobscraper.enrichment.classify_roles import classify_job
from jobscraper.job_boards._http import IncompleteBoardResponse
from jobscraper.job_boards.citadel.client import (
    CitadelBrowserError, CitadelChallengeError, CitadelNavigationError,
    fetch_citadel_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("citadel-probe.json"))
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument("--headed", action="store_true", help="manual diagnostic under Xvfb/display only")
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "failed", "snapshot_complete": False,
        "global_count": None, "sg_hk_count": None, "tracked_role_count": None,
        "page_count": None, "useful": False, "failure_reason": None,
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "commit": os.environ.get("GITHUB_SHA", ""),
    }
    try:
        snapshot = fetch_citadel_snapshot(
            diagnostics_dir=args.diagnostics_dir, headed=args.headed,
        )
        tracked = sum(classify_job(job) is not None for job in snapshot.jobs)
        report.update(
            snapshot_complete=True, global_count=snapshot.global_count,
            sg_hk_count=len(snapshot.jobs), tracked_role_count=tracked,
            page_count=snapshot.page_count, useful=tracked > 0,
            status="passed" if tracked else "empty",
            failure_reason=None if tracked else "No SG/HK postings match the tracked role categories",
        )
    except (CitadelBrowserError, CitadelChallengeError, CitadelNavigationError, IncompleteBoardResponse) as exc:
        report["failure_reason"] = str(exc)
    except Exception:
        # Raw Playwright errors may include URLs containing challenge tokens.
        report["failure_reason"] = "Unexpected probe failure; inspect the local browser setup"
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = (
        "## Citadel browser probe\n\n"
        f"Status: {report['status']}\n\n"
        f"Complete snapshot: {report['snapshot_complete']}\n\n"
        f"Global jobs: {report['global_count']}; SG/HK jobs: {report['sg_hk_count']}; "
        f"tracked roles: {report['tracked_role_count']}; pages: {report['page_count']}\n\n"
        f"Fetch duration: {report['elapsed_seconds']} seconds\n\n"
        f"{report['failure_reason'] or 'No human interaction was needed.'}\n\n"
        "Production activation requires three consecutive useful scheduled passes on main.\n"
    )
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
