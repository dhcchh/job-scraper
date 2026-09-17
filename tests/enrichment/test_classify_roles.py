"""Tests for classify.classify_job -- keyword routing and the noise default."""

import datetime

import pytest

from jobscraper.enrichment.classify_roles import classify_job
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
        # "quantitative analytics" wins over QT/QR's bare "quantitative" --
        # at a non-trading firm this names a DS/analytics function, not a
        # trading desk (confirmed against a real Stripe posting).
        ("Treasury Finance AI and Quantitative Analytics, Americas", "DS/ML/Algos"),
        # Still correctly QT/QR when the words aren't the adjacent phrase.
        ("Quantitative Technologist - Treasury Analytics", "QT/QR"),
        ("Site Reliability Engineer", "SRE/DevOps"),
        ("Reliability Engineer", "SRE/DevOps"),
        ("DevOps Engineer", "SRE/DevOps"),
        ("Platform Engineer", "SRE/DevOps"),
        # Big-tech-style titling (validated against real Crypto
        # Exchange/Gaming/Fintech/big-tech board data): bare/reordered
        # "Engineer" phrases and engineering
        # management, deliberately excluding customer-facing "Solutions
        # Engineer"/"Support Engineer" (see the noise test below).
        ("Staff Engineer, Core Infrastructure", "SWE/DE"),
        ("Senior Security Engineer, Detection and Response", "SWE/DE"),
        ("Engineering Manager, Notifications", "SWE/DE"),
        ("Director of Engineering, Infrastructure", "SWE/DE"),
        ("Manager, Software Engineering - Billing", "SWE/DE"),
        # "Forward Deployed Engineer" is a real hands-on engineering role
        # (confirmed via OpenAI Deployment Co's live board, where every
        # posting uses exactly this bare title with no other keyword-bearing
        # qualifier) -- unlike "Solutions Engineer"/"Support Engineer" below,
        # which stay noise as genuinely pre-sales/support titles.
        ("Forward Deployed Engineer - US", "SWE/DE"),
        ("Forward Deployed Engineer", "SWE/DE"),
        # Palantir's own more specific "Forward Deployed <X> Engineer"
        # titles already route correctly via their own compound keyword
        # (confirmed against Palantir's live board) -- no keyword-list
        # change needed for these, just confirming they still work.
        ("Forward Deployed Software Engineer", "SWE/DE"),
        ("Forward Deployed AI Engineer", "DS/ML/Algos"),
        ("Forward Deployed Infrastructure Engineer - UK Government", "SRE/DevOps"),
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
        # Customer-facing "Engineer" titles stay noise deliberately -- these
        # are pre-sales/support roles, not the hands-on-code or
        # engineering-management scope SWE/DE was expanded to cover. Unlike
        # "Forward Deployed Engineer" (see the routing test above), neither
        # of these names a hands-on delivery/engineering role.
        "Solutions Engineer, Privy",
        "Senior Support Engineer (GovOps)",
    ],
)
def test_classify_job_returns_none_for_noise(title):
    assert classify_job(_job(title)) is None


def test_classify_job_does_not_match_substring_inside_a_longer_word():
    # "engineer" inside "engineering" must not satisfy the whole-word match --
    # a recruiter for an engineering team is noise, not a job for SWE/DE.
    assert classify_job(_job("Data Engineering Recruiter")) is None


def test_classify_job_vetoes_recruiter_titles_even_with_a_real_keyword():
    # A "Recruiter" title must never route anywhere, regardless of what else
    # it mentions -- both are real postings that were wrongly landing in a
    # category via a bare "quant"/"machine learning" match before this veto.
    assert classify_job(_job("Lead Technical Recruiter (Quant Engineering)")) is None
    assert classify_job(
        _job("Campus Recruiter, Machine Learning and Quantitative Research")
    ) is None


def test_classify_job_does_not_veto_recruiting_as_a_department_qualifier():
    # "Recruiting" (not "recruiter") can trail a genuine IC title as a
    # department-style qualifier -- must not be swept up by the same veto.
    assert classify_job(_job("People Research Scientist, Recruiting")) == "DS/ML/Algos"


def test_classify_job_does_not_veto_recruiting_as_a_leading_qualifier():
    # Same veto-avoidance as above, opposite word order: "Recruiting" can
    # also lead a genuine IC title, naming the org the role supports rather
    # than being the role itself -- confirmed on Anthropic's live Greenhouse
    # board ("Recruiting Analytics Data Engineer", department "People", a
    # real data engineer role, not a recruiter).
    assert classify_job(
        _job("Recruiting Analytics Data Engineer", department="People")
    ) == "SWE/DE"


def test_classify_job_vetoes_solutions_architect_even_with_a_real_keyword():
    # Confirmed on Cloudflare's live Greenhouse board: both titles matched
    # SWE/DE's bare "developer" keyword via their own trailing "(Developer
    # Platform)" team/product name, not because the role is a developer
    # role -- a Solutions Architect title must never route anywhere,
    # regardless of what else it mentions.
    assert classify_job(
        _job("Principal Solutions Architect, APJC (Developer Platform) - Mandarin Speaking")
    ) is None
    assert classify_job(
        _job("Senior Solutions Architect, APJC (Developer Platform) - India Market")
    ) is None
    assert classify_job(_job("Partner Solutions Architect- Service Providers")) is None
    # "Architecture", not just "Architect", is caught by the same veto --
    # confirmed on GitLab's live board ("Manager, Solutions Architecture -
    # East", "Senior Manager, Solutions Architecture, AI & Developer
    # Platform").
    assert classify_job(_job("Manager, Solutions Architecture - East")) is None
    assert classify_job(
        _job("Senior Manager, Solutions Architecture, AI & Developer Platform")
    ) is None
