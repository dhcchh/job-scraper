"""Tests for classify.classify_job -- keyword routing and the noise default."""

import datetime

import pytest

from jobscraper.classifications.classify_roles import classify_job
from jobscraper.models import Job

_TODAY = datetime.date(2026, 9, 12)


def _job(title, department=""):
    return Job("acme", "1", title, department, "Remote", "https://x", "", _TODAY)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer", "SWE/DE"),
        ("Backend Engineer", "SWE/DE"),
        ("Data Engineer", "SWE/DE"),
        ("Analytics Engineer", "SWE/DE"),  # confirmed: DE/SWE, not DA/BI
        ("Quantitative Developer", "SWE/DE"),  # confirmed: SWE, not QT/QR
        ("Quant Developer", "SWE/DE"),  # bare "developer" wins over bare "quant"
        ("C++ Developer (Market Execution)", "SWE/DE"),
        ("Data Analyst", "DA/BI"),
        ("Business Analyst", "DA/BI"),
        ("BI Developer", "DA/BI"),  # "bi developer" wins over bare "developer"
        ("Data Scientist", "DS/ML/Algos"),
        ("Machine Learning Engineer", "DS/ML/Algos"),
        ("Algo Developer", "DS/ML/Algos"),  # confirmed: ML/Algo bucket, not SWE/DE
        ("Algorithm Engineer", "DS/ML/Algos"),
        ("Quantitative Trader", "QT/QR"),
        ("Quant Researcher", "QT/QR"),
        ("Quant Research Analyst", "QT/QR"),
        ("Quantitative Analyst", "QT/QR"),
        ("Quantitative Strategist", "QT/QR"),
        ("Front Office Pricing Modelling Quant", "QT/QR"),  # bare "quant"
        ("Market Microstructure Researcher", "QT/QR"),
        ("Site Reliability Engineer", "SRE/DevOps"),
        ("Reliability Engineer", "SRE/DevOps"),
        ("DevOps Engineer", "SRE/DevOps"),
    ],
)
def test_classify_job_routes_to_expected_category(title, expected):
    assert classify_job(_job(title)) == expected


def test_classify_job_ignores_department():
    # Department is deliberately excluded from matching: some boards' department
    # field is a broad org-unit label, not a reliable per-role signal -- e.g. a
    # real "Business Development Manager" posting sat under a department
    # literally named "Quant Management". Matching department too would have
    # wrongly routed it into QT/QR.
    job = _job("Business Development Manager", department="Quant Management")
    assert classify_job(job) is None

    job = _job("Generalist", department="Quantitative Research")
    assert classify_job(job) is None


@pytest.mark.parametrize(
    "title",
    [
        "HR Business Partner",
        "Staff Accountant",
        "Technical Recruiter",
        "Office Manager",
        "Executive Assistant",
    ],
)
def test_classify_job_returns_none_for_noise(title):
    assert classify_job(_job(title)) is None


def test_classify_job_does_not_match_substring_inside_a_longer_word():
    # "engineer" inside "engineering" must not satisfy the whole-word match --
    # a recruiter for an engineering team is noise, not a job for SWE/DE.
    assert classify_job(_job("Data Engineering Recruiter")) is None
