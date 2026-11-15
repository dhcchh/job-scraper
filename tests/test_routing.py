"""Tests for the tab routing shared by the scrape and the refreshes."""

import datetime

from jobscraper import routing
from jobscraper.models import Job


def _job(title, location):
    return Job("Acme", "1", title, "", location, "", "", datetime.date(2026, 11, 15))


def test_job_tabs_pair_every_category_with_every_region():
    assert len(routing.JOB_TABS) == len(set(routing.JOB_TABS)) == 10
    assert routing.JOB_TABS[:2] == ("DS/ML/Algos - PAC", "DS/ML/Algos - RoW")
    assert "SWE/DE - PAC" in routing.JOB_TABS


def test_route_job_names_the_tab_and_category_of_a_tracked_job():
    assert routing.route_job(_job("Software Engineer", "Singapore")) == (
        "SWE/DE - PAC",
        "SWE/DE",
    )
    assert routing.route_job(_job("Data Analyst", "London, United Kingdom")).tab == (
        "DA/BI - RoW"
    )


def test_route_job_reports_noise_as_no_tab_and_no_category():
    assert routing.route_job(_job("Account Executive", "Singapore")) == (None, None)


def test_route_job_keeps_the_category_of_a_job_excluded_by_country():
    # Callers tell the two apart: noise has no category, an excluded job does.
    assert routing.route_job(_job("Software Engineer", "Bangalore, India")) == (
        None,
        "SWE/DE",
    )


def test_route_job_keeps_a_job_with_one_office_outside_the_excluded_countries():
    job = _job("Software Engineer", "Bangalore, India | Singapore")
    assert routing.route_job(job).tab == "SWE/DE - PAC"
