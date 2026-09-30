"""Public fetch functions for every supported board provider."""

from collections.abc import Callable

from jobscraper import config
from jobscraper.job_boards.ashby.client import fetch_ashby_jobs
from jobscraper.job_boards.eightfold.client import fetch_eightfold_jobs
from jobscraper.job_boards.greenhouse.client import fetch_greenhouse_jobs
from jobscraper.job_boards.lever.client import fetch_lever_jobs
from jobscraper.job_boards.recruitee.client import fetch_recruitee_jobs
from jobscraper.job_boards.smartrecruiters.client import fetch_smartrecruiters_jobs
from jobscraper.job_boards.workday.client import fetch_workday_jobs
from jobscraper.models import Job

Fetcher = Callable[..., list[Job]]


def board_sources() -> list[tuple[str, list[dict], Fetcher]]:
    """Return each configured provider and its fetch function."""
    return [
        (name, boards, globals()[f"fetch_{name}_jobs"])
        for name, _label, boards in config.board_sources()
    ]


def fetch_board(source: str, board: dict, fetch: Fetcher) -> list[Job]:
    """Apply optional provider settings consistently in every command."""
    kwargs = (
        {"location_scope": board["location_scope"]}
        if source == "workday" and board.get("location_scope")
        else {}
    )
    return fetch(board["token"], board["firm"], board["company_type"], **kwargs)
