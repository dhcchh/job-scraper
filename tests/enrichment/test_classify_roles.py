"""Tests for classify.classify_job -- keyword routing and the noise default."""

import datetime

import pytest

from jobscraper.enrichment.classify_roles import classify_job
from jobscraper.models import Job

_TODAY = datetime.date(2026, 9, 12)


def _job(title, department="", company_type=""):
    return Job(
        "acme", "1", title, department, "Remote", "https://x", "", _TODAY,
        company_type=company_type,
    )


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
        # Surfaced by the run summary's unmatched-titles report (2026-09-27):
        # all were noise before.
        ("Software Engineering Internship (C++ or Python) - Summer 2027", "SWE/DE"),
        ("Director, Software Engineering", "SWE/DE"),
        ("Intern, Software Development Engineer [PSET-Access-ENG]", "SWE/DE"),
        ("Sr SW Engineer", "SWE/DE"),
        ("Staff Fullstack Engineer, User Auth Experience", "SWE/DE"),
        ("Android Engineer, Terminal", "SWE/DE"),
        ("iOS Engineer, Government Products", "SWE/DE"),
        ("Senior Java Engineer", "SWE/DE"),
        ("Python Engineer | Python Performance", "SWE/DE"),
        ("Cybersecurity Engineer", "SWE/DE"),
        ("Team Lead, Data Engineering", "SWE/DE"),
        ("Forward-Deployed Engineer", "SWE/DE"),
        ("Manager, Forward Deployed Engineering", "SWE/DE"),
        ("Data Science Intern", "DS/ML/Algos"),
        # Surfaced by a sweep of every board's unmatched titles (2026-10-17):
        # all were noise before.
        ("Staff Back-end Engineer (Rocket Pay)", "SWE/DE"),
        ("Sr. Staff Front-end Engineer  (Global Operations Technology)", "SWE/DE"),
        ("Front End Engineer, Marketing", "SWE/DE"),
        ("Server-Side Engineer (C++) - Pre-Trade Risk Technology", "SWE/DE"),
        ("Senior C++ Engineer - High Frequency Trading", "SWE/DE"),
        ("Kdb+ Engineer - Systematic Data Platform", "SWE/DE"),
        ("Product Engineer, Computer Use", "SWE/DE"),
        ("Growth Engineer", "SWE/DE"),
        ("Staff Web Engineer", "SWE/DE"),
        ("Blockchain Engineer - Binance Wallet", "SWE/DE"),
        ("Flutter Engineer (iOS)", "SWE/DE"),
        ("Tools & Compilers Research and Development", "SWE/DE"),
        ("Trading Engineer - Execution", "SWE/DE"),
        ("Research & Portfolio Management Engineering - Associate", "SWE/DE"),
        ("Senior Cloud Networking Engineer", "SWE/DE"),
        ("Director, Analytics Engineering", "SWE/DE"),
        ("Deep Learning Researcher | Trading Team", "DS/ML/Algos"),
        ("Member of Technical Staff, Post-Training", "DS/ML/Algos"),
        ("AI Scientist - Physics Models", "DS/ML/Algos"),
        ("LLM Scientist, AI for Science", "DS/ML/Algos"),
        ("Campus AI Researcher, PhD/Postdoc (Intern)", "DS/ML/Algos"),
        ("Research MLE (Training Optimization)", "DS/ML/Algos"),
        ("Inference Engineer, Robotics", "DS/ML/Algos"),
        ("AI Agent  Engineer", "DS/ML/Algos"),  # runs of spaces collapse
        ("Research Engineering Lead", "DS/ML/Algos"),
        ("Experienced Options Trader – Singapore Office", "QT/QR"),
        ("Graduate Trader", "QT/QR"),
        ("Systematic Trader", "QT/QR"),
        ("Futures & Options Trading Analyst Intern - Summer 2027", "QT/QR"),
        ("Trading Intern", "QT/QR"),
        ("Electronic Trader Analyst", "QT/QR"),
        ("Linux Engineer", "SRE/DevOps"),
        ("Trading Desk Operations Engineer", "SRE/DevOps"),
        ("Systematic Production Support Engineer", "SRE/DevOps"),
        ("HPC Operations Engineer", "SRE/DevOps"),
        ("Network Operations Engineer (AWS)", "SRE/DevOps"),
        ("Senior Database Administrator (DBA), Infra", "SRE/DevOps"),
        ("Lead Database Engineer", "SRE/DevOps"),
        ("Cloud Infra Engineer", "SRE/DevOps"),
        ("2027 – Internship, Infrastructure Engineering", "SRE/DevOps"),
        ("Trading Infrastructure Specialist – Graduate Programme", "SRE/DevOps"),
        # Generic senior titles are a fallback after every category.
        ("Engineering Lead / Manager, KYC", "SWE/DE"),
        ("Engineering Director, Mobile (iOS/Android)", "SWE/DE"),
        ("Engineering Leader - Payments APAC", "SWE/DE"),
        ("Distinguished Engineer, Architecture & Engineering Excellence", "SWE/DE"),
        ("Principal Engineer, Authentication", "SWE/DE"),
        ("Member of Technical Staff - SDK", "SWE/DE"),
        ("2027 Engineering Summer Analyst", "SWE/DE"),
        ("Engineering Intern (Summer 2027)", "SWE/DE"),
        ("2027 Platform Engineering Intern", "SRE/DevOps"),
        ("Distinguished Engineer, Core DevOps", "SRE/DevOps"),
        ("Member of Technical Staff (AI Infrastructure Engineer)", "SRE/DevOps"),
        # So is bare "analytics".
        ("Senior Product Analytics Manager - Online Platforms", "DA/BI"),
        ("Analyst (Supply Analytics, Bangkok-based, Relocation provided)", "DA/BI"),
        ("Portfolio Analytics Engineer - Vice President", "SWE/DE"),
    ],
)
def test_classify_job_routes_to_expected_category(title, expected):
    assert classify_job(_job(title)) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Junior Discretionary Trader",
        "ETF Sales Trader",
        "OTC Trader - Digital Assets",
        "Floor Trader",
        "Repo Trader",
        "Equity Financing Trader, Asia Pacific",
        "Portfolio Finance Trader",
        "Trader Assistant (Working Student)",
        "Commodities Broker Trader",
        "Associate Head of Trader Education",
        "Customer Experience Representative, Active Trader",
    ],
)
def test_classify_job_keeps_non_quant_traders_as_noise(title):
    assert classify_job(_job(title)) is None


@pytest.mark.parametrize(
    "title",
    [
        "Technical Support Engineering Leader, Singapore",
        "Dedicated Support Engineering Lead",
        "Solutions Engineering Lead",
        "Systems Engineering Lead, New Product Initiatives",
        "Staff Silicon Product Engineer",
        "Data Center Infrastructure Engineering Program Manager",
        "Senior Product Manager - Interactive Analytics",
        "Principal, Strategic Finance & Analytics, Acquisition",
        "Advisory Solution Consultant - Data & Analytics",
    ],
)
def test_classify_job_keeps_new_keywords_off_non_engineering_titles(title):
    assert classify_job(_job(title)) is None


@pytest.mark.parametrize(
    "title",
    [
        "2027 Research Summer Analyst",
        "Global Stock Selection Research – Technical Signals",
        "Senior Member of Research Staff, Optimization",
        "Campus Crypto Researcher (Intern)",
        "Portfolio Researcher",
        "VIE in Research",
    ],
)
def test_classify_job_routes_bare_research_at_trading_firms_to_qt_qr(title):
    assert classify_job(_job(title, company_type="Prop Trading/Hedge Fund")) == "QT/QR"
    # The same title elsewhere is too ambiguous to keep.
    assert classify_job(_job(title, company_type="Fintech")) is None


@pytest.mark.parametrize(
    "title", ["Researcher, Alignment", "Research, General Agents", "Research Intern"]
)
def test_classify_job_routes_bare_research_at_ai_labs_to_ds_ml(title):
    assert classify_job(_job(title, company_type="AI Lab")) == "DS/ML/Algos"


@pytest.mark.parametrize(
    "title,company_type",
    [
        ("Fundamental Researcher - Canvas", "Prop Trading/Hedge Fund"),
        ("Equity Research Analyst, China Industrials", "Prop Trading/Hedge Fund"),
        ("Macro Research Analyst", "Prop Trading/Hedge Fund"),
        ("Client Investment Research Associate", "Prop Trading/Hedge Fund"),
        ("Corporate Access & Research Coordinator", "Prop Trading/Hedge Fund"),
        ("Research Data Expert", "Prop Trading/Hedge Fund"),
        ("Research Economist, Economic Research", "AI Lab"),
        ("Research Program Manager, Governance", "AI Lab"),
        ("User Experience Researcher, Platform", "AI Lab"),
        ("Market Research Lead, ChatGPT", "AI Lab"),
        ("Research Counsel", "AI Lab"),
    ],
)
def test_classify_job_keeps_non_technical_research_as_noise(title, company_type):
    assert classify_job(_job(title, company_type=company_type)) is None


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


def test_classify_job_vetoes_legal_engineer_titles():
    # Confirmed on Harvey's live Ashby board: a legal engineer is a lawyer in
    # a customer-facing solutions role; this one matched "engineering manager".
    assert classify_job(_job("Legal Engineering Manager, APAC")) is None
    assert classify_job(_job("Head of Legal Engineering, EMEA")) is None
    assert classify_job(_job("Legal Engineer, Product Specialist")) is None


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


@pytest.mark.parametrize(
    "title",
    [
        "Sr. Product Manager, Data Engineering",
        "Sr. Technical Program Manager, Data Engineering",
        "Data Engineering Solutions Specialist",
        "Product Owner - Data Engineer Platform",
    ],
)
def test_classify_job_keeps_de_for_proper_data_engineering_roles(title):
    # A data-engineering keyword on a PM/pre-sales title names the team.
    assert classify_job(_job(title)) is None


@pytest.mark.parametrize(
    "title",
    [
        "Manager, Data Engineering",  # manages the engineers -- a DE role
        "Data Engineer - Analyst",
        "Technology Associate Programme - Data Engineering",
    ],
)
def test_classify_job_still_routes_real_data_engineering_roles(title):
    assert classify_job(_job(title)) == "SWE/DE"


def test_de_veto_leaves_other_keywords_alone():
    # Only the DE keywords are skipped; another category's keyword still counts.
    title = "Technical Program Manager, Data Engineering & Machine Learning"
    assert classify_job(_job(title)) == "DS/ML/Algos"
