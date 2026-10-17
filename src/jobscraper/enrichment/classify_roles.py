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
            "deep learning",
            "reinforcement learning",
            "post-training",
            "pre-training",
            "ai scientist",
            "llm scientist",
            "ai researcher",
            "research engineering",
            "mle",
            "inference engineer",
            "inference engineering",
            "ai agent engineer",
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
            "back-end",
            "back end",
            "frontend",
            "front-end",
            "front end",
            "server-side engineer",
            "server side engineer",
            "full stack",
            "full-stack",
            "fullstack",
            "mobile engineer",
            "android engineer",
            "ios engineer",
            "java engineer",
            "python engineer",
            "c++ engineer",
            "kdb",
            "application engineer",
            "product engineer",
            "growth engineer",
            "web engineer",
            "blockchain engineer",
            "flutter engineer",
            "developer experience engineer",
            "compiler",
            "compilers",
            "trading engineer",
            "portfolio management engineer",
            "portfolio management engineering",
            "data engineer",
            "data engineering",
            "etl engineer",
            "big data engineer",
            "analytics engineer",
            "analytics engineering",
            "fpga",
            "security engineer",
            "cybersecurity engineer",
            "network engineer",
            "networking engineer",
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
            "trader",
            "trading analyst",
            "trading intern",
            "trading internship",
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
            "platform engineering",
            "infrastructure engineer",
            "systems engineer",
            "cloud engineer",
            "release engineer",
            "build engineer",
            "production engineer",
            "infrastructure engineering",
            "infra engineer",
            "infrastructure specialist",
            "linux engineer",
            "windows engineer",
            "storage engineer",
            "database engineer",
            "database administrator",
            "hpc operations engineer",
            "storage operations engineer",
            "network operations engineer",
            "trading operations engineer",
            "desk operations engineer",
            "production support engineer",
        ),
    ),
]

# Recruiter, solutions-architect(ure) and legal-engineer titles are noise
# whatever else they mention. A "legal engineer" (Harvey's term) is a lawyer
# in a customer-facing solutions role, not an engineer -- "Legal Engineering
# Manager, APAC" was landing in SWE/DE via "engineering manager".
_NOISE_TITLE_RE = re.compile(
    r"\brecruiter\b|\bsolutions architect|\blegal engineer", re.IGNORECASE
)

# On a product/program manager or pre-sales title, a data-engineering keyword
# names the team, not the job ("Sr. Product Manager, Data Engineering") --
# DE is meant for proper data-engineering roles only. The same goes for the
# other team-name "... engineering" keywords, and for "developer" on a
# "Product Manager, Developer Productivity" title.
_TEAM_KEYWORDS = frozenset(
    {
        "developer",
        "data engineer",
        "data engineering",
        "big data engineer",
        "etl engineer",
        "analytics engineering",
        "research engineering",
        "inference engineering",
        "infrastructure engineering",
        "platform engineering",
        "portfolio management engineering",
    }
)
_NOT_AN_ENGINEERING_ROLE_RE = re.compile(
    r"\b(?:product|program|project) manager\b|\bproduct owner\b"
    r"|\bsolutions specialist\b",
    re.IGNORECASE,
)

# QT/QR takes systematic and market-making traders, not discretionary,
# sales, OTC or floor traders, financing desks or trading operations.
# Robinhood's "Active Trader" is a product name on support titles.
_TRADER_KEYWORDS = frozenset(
    {
        "trader",
        "execution trader",
        "trading analyst",
        "trading intern",
        "trading internship",
    }
)
_NOT_A_QUANT_TRADER_RE = re.compile(
    r"\b(?:discretionary|sales|otc|floor|repo|financing|finance|assistant"
    r"|operations|support|broker|education|customer)\b",
    re.IGNORECASE,
)

# Waymo's "Silicon Product Engineer" is a chip role.
_HARDWARE_RE = re.compile(r"\b(?:silicon|hardware|manufacturing)\b", re.IGNORECASE)

# "Developer" names the audience, not the job, on developer relations,
# advocacy, experience, marketing and similar titles ("Developer Advocate",
# "Developer Marketing Lead, APAC"). Their engineers are customer-facing like
# solutions and support engineers, so they stay noise too; developer
# experience engineers build internal tooling and keep their own keyword.
# Business, land and property developers don't write software at all.
_NOT_A_SOFTWARE_DEVELOPER_RE = re.compile(
    r"\bdeveloper (?:relations|advocates?|advocacy|experiences?|success|support"
    r"|community|engagement|connections|education|marketing|evangelists?"
    r"|ecosystems?|programs?|audiences?|gtm)\b"
    r"|\b(?:business|curriculum|content|land|property) developer\b"
    r"|\bdeveloper/agency\b",
    re.IGNORECASE,
)

# Leads of customer-facing engineering teams are noise: "Solutions
# Engineering Lead", "Sales Engineering Manager".
_NOT_SOFTWARE_ENGINEERING_RE = re.compile(
    r"\b(?:support|solutions?|field|sales|legal|operations|vendor|systems)"
    r" engineering\b",
    re.IGNORECASE,
)

_KEYWORD_VETOES: list[tuple[re.Pattern[str], frozenset[str]]] = [
    (_NOT_AN_ENGINEERING_ROLE_RE, _TEAM_KEYWORDS),
    (_NOT_A_QUANT_TRADER_RE, _TRADER_KEYWORDS),
    (_HARDWARE_RE, frozenset({"product engineer"})),
    (_NOT_A_SOFTWARE_DEVELOPER_RE, frozenset({"developer"})),
    (_NOT_SOFTWARE_ENGINEERING_RE, frozenset({"engineering manager"})),
]

# Generic senior titles name no specialism, so they count only after every
# category's keywords: "Distinguished Engineer, Core DevOps" and "Staff
# Engineer, Site Reliability" stay SRE/DevOps.
_GENERIC_ENGINEERING_KEYWORDS = (
    "member of technical staff",
    "staff engineer",
    "principal engineer",
    "distinguished engineer",
    "engineering director",
    "engineering lead",
    "engineering leader",
    "engineering intern",
    "engineering internship",
    "engineering summer analyst",
)

# A bare "Research"/"Researcher" title names quant research at a trading firm
# ("2027 Research Summer Analyst", "Member of Research Staff") and ML research
# at an AI lab. Elsewhere it is too often UX, market or economic research.
# Checked only after every keyword, so "Research Engineer" etc. keep theirs.
_RESEARCH_CATEGORY_BY_COMPANY_TYPE = {
    "Prop Trading/Hedge Fund": "QT/QR",
    "AI Lab": "DS/ML/Algos",
}
_RESEARCH_RE = re.compile(r"\bresearch(?:ers?)?\b", re.IGNORECASE)
_NOT_TECHNICAL_RESEARCH_RE = re.compile(
    r"\b(?:fundamental|equity|credit|macro|sector|client|economic|economist"
    r"|market|policy|political|ux|user|product|program|operations|support"
    r"|coordinator|partnerships|counsel|communications|marketing|sales"
    r"|talent|data expert)\b",
    re.IGNORECASE,
)

# Bare "analytics" is checked last too, so "Analytics Engineer" stays SWE/DE
# and "Quantitative Analytics" stays DS/ML/Algos.
_ANALYTICS_RE = re.compile(r"\banalytics\b", re.IGNORECASE)
_NOT_DATA_ANALYTICS_RE = re.compile(
    r"\bfinance\b|\bconsultant\b", re.IGNORECASE
)


def classify_job(job: Job) -> str | None:
    """Return the first matching role category, or None for noise."""
    text = " ".join(job.title.split())
    if _NOISE_TITLE_RE.search(text):
        return None
    skipped = frozenset().union(
        *(keywords for pattern, keywords in _KEYWORD_VETOES if pattern.search(text))
    )
    for category, keywords in CATEGORIES:
        if any(
            contains_keyword(text, keyword)
            for keyword in keywords
            if keyword not in skipped
        ):
            return category
    if not _NOT_SOFTWARE_ENGINEERING_RE.search(text) and any(
        contains_keyword(text, keyword) for keyword in _GENERIC_ENGINEERING_KEYWORDS
    ):
        return "SWE/DE"
    research_category = _RESEARCH_CATEGORY_BY_COMPANY_TYPE.get(job.company_type)
    if (
        research_category is not None
        and _RESEARCH_RE.search(text)
        and not _NOT_TECHNICAL_RESEARCH_RE.search(text)
    ):
        return research_category
    if (
        _ANALYTICS_RE.search(text)
        and not _NOT_AN_ENGINEERING_ROLE_RE.search(text)
        and not _NOT_DATA_ANALYTICS_RE.search(text)
    ):
        return "DA/BI"
    return None
