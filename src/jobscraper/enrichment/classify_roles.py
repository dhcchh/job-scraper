"""Classify jobs by title; unmatched jobs are treated as noise."""

import re

from jobscraper.enrichment._keywords import contains_keyword

from jobscraper.models import Job

# First matching category wins, so this order is significant.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "DS/ML/Algos",
        (
            "algo",
            "algorithm",
            "data scientist",
            "data science",
            "machine learning",
            "ml",
            "applied scientist",
            "research scientist",
            "research engineer",
            "ai engineer",
            "nlp",
            "computer vision",
            "statistician",
            "quantitative analytics",
        ),
    ),
    (
        "DA/BI",
        (
            "data analyst",
            "business analyst",
            "bi analyst",
            "bi developer",
            "business intelligence",
            "reporting analyst",
            "insights analyst",
            "product analyst",
            "marketing analyst",
            "growth analyst",
        ),
    ),
    (
        "SWE/DE",
        (
            "software engineer",
            "software engineering",
            "software development engineer",
            "sw engineer",
            "developer",  # broad on purpose -- covers every "X Developer" title
            "backend",
            "frontend",
            "full stack",
            "full-stack",
            "fullstack",
            "mobile engineer",
            "android engineer",
            "ios engineer",
            "java engineer",
            "python engineer",
            "application engineer",
            "data engineer",
            "data engineering",
            "etl engineer",
            "big data engineer",
            "analytics engineer",
            "fpga",
            "staff engineer",
            "security engineer",
            "cybersecurity engineer",
            "network engineer",
            "firmware engineer",
            "performance engineer",
            "forward deployed engineer",
            "forward-deployed engineer",
            "forward deployed engineering",
            "engineering manager",
            "director of engineering",
            "director, engineering",
            "vice president of engineering",
            "vp of engineering",
            "head of engineering",
            "manager, engineering",
            "manager, software engineering",
        ),
    ),
    (
        "QT/QR",
        (
            "quant",
            "quantitative",
            "execution trader",
            "microstructure researcher",
        ),
    ),
    (
        "SRE/DevOps",
        (
            "site reliability",
            "reliability engineer",
            "sre",
            "devops",
            "platform engineer",
            "infrastructure engineer",
            "systems engineer",
            "cloud engineer",
            "release engineer",
            "build engineer",
            "production engineer",
        ),
    ),
]

_RECRUITER_RE = re.compile(r"\brecruiter\b", re.IGNORECASE)

_SOLUTIONS_ARCHITECT_RE = re.compile(r"\bsolutions architect", re.IGNORECASE)

# A "legal engineer" (Harvey's term) is a lawyer in a customer-facing
# solutions role, not an engineer -- "Legal Engineering Manager, APAC" was
# landing in SWE/DE via "engineering manager".
_LEGAL_ENGINEER_RE = re.compile(r"\blegal engineer", re.IGNORECASE)

# On a product/program manager or pre-sales title, a data-engineering keyword
# names the team, not the job ("Sr. Product Manager, Data Engineering") --
# DE is meant for proper data-engineering roles only.
_DE_KEYWORDS = frozenset(
    {"data engineer", "data engineering", "big data engineer", "etl engineer"}
)
_NOT_AN_ENGINEERING_ROLE_RE = re.compile(
    r"\b(?:product|program|project) manager\b|\bproduct owner\b"
    r"|\bsolutions specialist\b",
    re.IGNORECASE,
)


def classify_job(job: Job) -> str | None:
    """Return the first matching role category, or None for noise."""
    text = job.title
    if _RECRUITER_RE.search(text):
        return None
    if _SOLUTIONS_ARCHITECT_RE.search(text) or _LEGAL_ENGINEER_RE.search(text):
        return None
    skipped = _DE_KEYWORDS if _NOT_AN_ENGINEERING_ROLE_RE.search(text) else ()
    for category, keywords in CATEGORIES:
        if any(
            contains_keyword(text, keyword)
            for keyword in keywords
            if keyword not in skipped
        ):
            return category
    return None
