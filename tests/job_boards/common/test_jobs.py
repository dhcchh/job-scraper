"""Tests for the job building shared by every provider client."""

import datetime

import pytest

from jobscraper.job_boards.common import jobs

_DATE = datetime.date(2026, 11, 15)


def _build(firm="Acme", **fields):
    values = {"job_id": 7, "title": "Senior Data Engineer", "location": "Singapore", "url": "https://x"}
    return jobs.build_job("greenhouse", firm, "Tech", _DATE, **{**values, **fields})


def test_build_job_derives_identity_source_and_seniority():
    job = _build(department="Platform")

    assert (job.board, job.job_id, job.company_type) == ("Acme", "7", "Tech")
    assert job.source == "Greenhouse"
    assert job.seniority == "Senior"
    assert job.scraped_date == _DATE
    assert (job.updated_at, job.first_published, job.description, job.salary) == ("", "", "", None)


def test_build_job_falls_back_to_employment_type_for_seniority():
    assert _build(title="Software Engineer", employment_type="Intern").seniority == "Intern"


def test_build_job_rejects_a_missing_job_id():
    with pytest.raises(ValueError):
        _build(job_id=None)


def test_build_job_reads_a_description_only_for_a_firm_needing_one(monkeypatch):
    monkeypatch.setattr(jobs, "FIRMS_NEEDING_DESCRIPTION", frozenset({"Listed"}))
    calls = []

    def describe():
        calls.append(1)
        return "The role."

    assert _build(firm="Acme", description=describe).description == ""
    assert calls == []  # not even extracted for a firm off the allowlist
    assert _build(firm="Listed", description=describe).description == "The role."
    assert _build(firm="Listed").description == ""


def test_jobs_from_dates_jobs_today_unless_given_a_date():
    def to_job(raw, firm, company_type, scraped_date):
        return jobs.build_job(
            "lever", firm, company_type, scraped_date,
            job_id=raw["id"], title="Engineer", location="", url="",
        )

    today, given = (
        jobs.jobs_from("lever", "acme", [{"id": "a"}, {"id": "b"}], to_job, "Acme", "Tech", date)
        for date in (None, _DATE)
    )

    assert [job.job_id for job in today] == ["a", "b"]
    assert {job.scraped_date for job in today} == {datetime.date.today()}
    assert {job.scraped_date for job in given} == {_DATE}


def test_html_sections_joins_non_empty_fields_as_plain_text():
    raw = {"a": "<p>First</p>", "b": "", "c": None, "d": "<ul><li>Second</li></ul>"}

    assert jobs.html_sections(raw, ("a", "b", "c", "d", "missing")) == "First\n\n- Second"
