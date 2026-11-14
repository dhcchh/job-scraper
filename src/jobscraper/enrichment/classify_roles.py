"""Classify jobs by title; unmatched jobs are treated as noise."""

import dataclasses
import re

from jobscraper.board_config import PROVIDER_LABELS
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

# On a product/program manager, technical writer, service delivery or
# pre-sales title, a data-engineering keyword names the team, not the job
# ("Sr. Product Manager, Data Engineering") -- DE is meant for proper
# data-engineering roles only. The same goes for the other team-name
# "... engineering" keywords, and for "developer" on a "Product Manager,
# Developer Productivity" title.
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
    r"\b(?:product|program|project) managers?\b|\bproduct owners?\b"
    r"|\bsolutions specialists?\b|\btechnical writers?\b"
    r"|\bservice delivery managers?\b",
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

def _keyword_vetoes(
    team_keywords: frozenset[str],
) -> tuple[tuple[re.Pattern[str], frozenset[str]], ...]:
    """Pair each veto pattern with the keywords it removes from a title."""
    return (
        (_NOT_AN_ENGINEERING_ROLE_RE, team_keywords),
        (_NOT_A_QUANT_TRADER_RE, _TRADER_KEYWORDS),
        (_HARDWARE_RE, frozenset({"product engineer"})),
        (_NOT_A_SOFTWARE_DEVELOPER_RE, frozenset({"developer"})),
        (_NOT_SOFTWARE_ENGINEERING_RE, frozenset({"engineering manager"})),
    )


# A provider's job family is the weakest signal, so it never makes a title
# SWE/DE when the title says the job isn't software engineering: a veto
# removed one of its keywords ("Developer Advocate", "Sales Engineering
# Manager", "Hardware Product Engineer"), it names a non-engineering role
# ("Product Owner, Payments") or a customer-facing engineering team
# ("Solutions Engineering Lead"), or it names a customer-facing engineer,
# a user researcher, a designer or a writer.
_NOT_A_SOFTWARE_ROLE_RE = re.compile(
    r"\b(?:support|solutions?|sales|pre-?sales|field|customer|partner)"
    r" engineers?\b"
    r"|\b(?:ux|user experience|user) research|\bdesigners?\b|\bwriters?\b",
    re.IGNORECASE,
)

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


@dataclasses.dataclass(frozen=True, slots=True)
class _SourceRules:
    """The title rules for one provider's jobs, its own merged into the shared.

    `noise` makes a whole title noise. `swe_department` names the department
    whose otherwise unmatched postings are SWE/DE.
    """

    categories: tuple[tuple[str, tuple[str, ...]], ...]
    keyword_vetoes: tuple[tuple[re.Pattern[str], frozenset[str]], ...]
    noise: re.Pattern[str] | None = None
    swe_department: str | None = None


def _source_rules(
    extra_keywords: dict[str, tuple[str, ...]] | None = None,
    *,
    team_keywords: frozenset[str] = frozenset(),
    noise: re.Pattern[str] | None = None,
    swe_department: str | None = None,
) -> _SourceRules:
    """Merge a provider's own keywords into the shared rules, built once.

    Each extra keyword joins its category in CATEGORIES order. Extra
    keywords that can name a team rather than the job go in `team_keywords`
    too, so a product owner's or writer's title doesn't match on them.
    """
    extra_keywords = extra_keywords or {}
    unknown = set(extra_keywords) - {category for category, _keywords in CATEGORIES}
    if unknown:
        raise ValueError(f"unknown role categories: {sorted(unknown)}")
    return _SourceRules(
        categories=tuple(
            (category, (*keywords, *extra_keywords.get(category, ())))
            for category, keywords in CATEGORIES
        ),
        keyword_vetoes=_keyword_vetoes(_TEAM_KEYWORDS | team_keywords),
        noise=noise,
        swe_department=swe_department,
    )


_SHARED_RULES = _source_rules()

# Amazon titles roles in its own words ("Software Dev Engineer", "SDE II",
# "SDM", "BIE", "System Development Engineer") and lists data-centre, QA and
# consulting roles under the engineering categories it is fetched from.
# These rules apply only to jobs from the Amazon provider, keyed by
# `Job.source`, so every other firm keeps the shared rules above.
_AMAZON_RULES = _source_rules(
    {
        "DS/ML/Algos": ("applied science", "research science"),
        "DA/BI": ("bie", "business intel engineer"),
        "SWE/DE": (
            # "Software Dev Eng", "Manager III, Software Development",
            # "Software Development II" and "Director, Software Development"
            # all omit or abbreviate "Engineer", so match the bare phrase.
            "software dev",
            "software development",
            # A live title misspells it ("Software Devevelopment Manager").
            "software devevelopment",
            "sde",
            # Level suffixes fuse onto the abbreviation ("SDE2", "SDEII").
            "sde1",
            "sde2",
            "sde3",
            "sdei",
            "sdeii",
            "sdeiii",
            "esde",
            "sdm",
            "network development engineer",
            "network dev engineer",
        ),
        "SRE/DevOps": (
            "system development engineer",
            "systems development engineer",
            "system dev engineer",
            "systems dev engineer",
            "sysdev engineer",
            "sys dev engineer",
        ),
    },
    # The bare phrases also name teams ("Product Owner, Software Development
    # Tools"), and "SDM" also abbreviates Service Delivery Manager.
    team_keywords=frozenset(
        {"software dev", "software development", "software devevelopment", "sdm"}
    ),
    # Amazon noise: every data-centre role, software ones included;
    # Infrastructure Reliability & Quality (IRQ) hardware-reliability roles;
    # QA and SDET roles; technicians; ProServe delivery consultants; physical
    # security; and program, project and product managers. Data-centre team
    # codes are matched case-sensitively and only as seen on data-centre
    # titles: "DCSX" (Devices Sales & Customer Experience) is not one, and
    # "DC" after "Washington" is the city.
    noise=re.compile(
        r"\bdata[ -]?cent(?:er|re)s?\b"
        r"|(?<!washington )(?<!washington, )(?<!washington,)"
        r"(?-i:\b(?:Inter)?DC(?:C|CD|E|EO|LC|NE|O|OSE|PD)?\b)"
        r"|(?-i:\bDRES\b)"
        r"|\binfrastructure reliability (?:&|and) quality\b|\bIRQ\b"
        r"|\bhardware reliability\b|\btechnicians?\b"
        r"|\bSDETs?\b|\bengineers? in test\b|\bquality assurance\b|\bQAEs?\b"
        r"|\bquality engineer|\btest engineer|\btesters?\b|\bsoftware quality\b"
        r"|\bdelivery consultants?\b|\bphysical security\b"
        r"|\b(?:program|project|product) manag(?:ers?|ement)\b|\bTPMs?\b",
        re.IGNORECASE,
    ),
    # Amazon files every posting under a job family (carried in
    # `Job.department`), and "Software Development" is its engineering one.
    # Titles there often name neither "software" nor a keyword
    # ("Kernel/Hypervisor Engineer", "Application Builder", "Tech Lead Eng"),
    # so a posting filed under that family is SWE/DE once the noise rules
    # and every title keyword have had their say, unless its title names a
    # job that isn't software engineering (see _NOT_A_SOFTWARE_ROLE_RE).
    swe_department="Software Development",
)

_RULES_BY_SOURCE: dict[str, _SourceRules] = {
    PROVIDER_LABELS["amazon"]: _AMAZON_RULES,
}


def classify_job(job: Job) -> str | None:
    """Return the first matching role category, or None for noise."""
    text = " ".join(job.title.split())
    if _NOISE_TITLE_RE.search(text):
        return None
    rules = _RULES_BY_SOURCE.get(job.source, _SHARED_RULES)
    if rules.noise is not None and rules.noise.search(text):
        return None
    skipped = frozenset().union(
        *(keywords for pattern, keywords in rules.keyword_vetoes if pattern.search(text))
    )
    for category, keywords in rules.categories:
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
    if (
        rules.swe_department is not None
        and job.department == rules.swe_department
        and not any(contains_keyword(text, keyword) for keyword in skipped)
        and not _NOT_AN_ENGINEERING_ROLE_RE.search(text)
        and not _NOT_SOFTWARE_ENGINEERING_RE.search(text)
        and not _NOT_A_SOFTWARE_ROLE_RE.search(text)
    ):
        return "SWE/DE"
    return None
