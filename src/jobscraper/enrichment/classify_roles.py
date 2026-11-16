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


# A provider's job family or team is the weakest signal, so it never gives a
# title a role when the title says the job isn't software engineering: a veto
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
class _DepartmentFallback:
    """An otherwise unmatched posting in `department` is `category`.

    With `title`, only when the title also matches it.
    """

    department: str
    category: str
    title: re.Pattern[str] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _SourceRules:
    """The title rules for one provider's jobs, its own merged into the shared.

    `noise` makes a whole title noise. `department_fallbacks` are checked in
    order once every keyword has had its say, and `fallback_veto` names
    titles none of them applies to.
    """

    categories: tuple[tuple[str, tuple[str, ...]], ...]
    keyword_vetoes: tuple[tuple[re.Pattern[str], frozenset[str]], ...]
    noise: re.Pattern[str] | None = None
    department_fallbacks: tuple[_DepartmentFallback, ...] = ()
    fallback_veto: re.Pattern[str] | None = None


def _source_rules(
    extra_keywords: dict[str, tuple[str, ...]] | None = None,
    *,
    team_keywords: frozenset[str] = frozenset(),
    noise: re.Pattern[str] | None = None,
    department_fallbacks: tuple[_DepartmentFallback, ...] = (),
    fallback_veto: re.Pattern[str] | None = None,
) -> _SourceRules:
    """Merge a provider's own keywords into the shared rules, built once.

    Each extra keyword joins its category in CATEGORIES order. Extra
    keywords that can name a team rather than the job go in `team_keywords`
    too, so a product owner's or writer's title doesn't match on them.
    """
    extra_keywords = extra_keywords or {}
    categories = {category for category, _keywords in CATEGORIES}
    unknown = (
        set(extra_keywords)
        | {fallback.category for fallback in department_fallbacks}
    ) - categories
    if unknown:
        raise ValueError(f"unknown role categories: {sorted(unknown)}")
    return _SourceRules(
        categories=tuple(
            (category, (*keywords, *extra_keywords.get(category, ())))
            for category, keywords in CATEGORIES
        ),
        keyword_vetoes=_keyword_vetoes(_TEAM_KEYWORDS | team_keywords),
        noise=noise,
        department_fallbacks=department_fallbacks,
        fallback_veto=fallback_veto,
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
    department_fallbacks=(_DepartmentFallback("Software Development", "SWE/DE"),),
)

# Apple names each internship by its type: "Software PhD Internships",
# "Hardware Undergrad Engineering Internships", "Legal Internships". An
# internship whose title names a non-tech type is noise, whatever keyword it
# also matches ("CAD Virtuoso Engineering Intern"), unless the title also
# names software, ML/AI, data science or security work ("Software
# Engineering Intern - AI Tools for Hardware Engineering").
_APPLE_INTERNSHIP = r"\binterns?(?:hips?)?\b"
_APPLE_NON_TECH_INTERNSHIP = (
    r"\b(?:hardware|analog|circuits?|silicon|RTL|PMU|SoC|CAD|physical design"
    r"|product design|mechanical|manufacturing|tooling|molding|vibration"
    r"|design for test|verification|validation|field test"
    r"|business|marketing|creative|finance|legal|MBA|sales|music|media|travel"
    r"|sourcing|supply|operations|(?:program|project) manag\w*|environment"
    r"|events|career fair|apple services)\b"
    r"|(?-i:\b(?:EE|RF(?:HW)?)\b(?!&))"
)
_APPLE_TECH_INTERNSHIP = (
    r"\b(?:software|machine learning|ML|AI|AIML|LLMs?|data science"
    r"|computer vision|security|information systems)\b"
)
_APPLE_NON_TECH_INTERNSHIP_RE = (
    rf"^(?=.*{_APPLE_INTERNSHIP})(?=.*(?:{_APPLE_NON_TECH_INTERNSHIP}))"
    rf"(?!.*(?:{_APPLE_TECH_INTERNSHIP}))"
)

# Apple titles name a specialism instead of a role keyword ("Swift
# Engineer", "Linux Kernel Engineer", "CVML Engineer") and list engineering
# program managers, QA and hardware roles under its software teams. These
# rules apply only to jobs from the Apple provider.
_APPLE_RULES = _source_rules(
    {
        "DS/ML/Algos": ("ai research", "aiml research", "applied researcher"),
        "SWE/DE": (
            "security researcher",
            "security research",
            "vulnerability researcher",
            # The IS&T (Apple's IT) internships name only the organisation.
            "information systems and technology",
        ),
    },
    team_keywords=frozenset({"information systems and technology"}),
    # Apple noise: QA and SDET roles, as for Amazon; Siri's language
    # engineers and experts, who are linguists; program, project and product
    # managers, including EPMs and TPMs; and electrical, RF,
    # mechanical-design and chip physical-design roles; and non-tech
    # internships (see _APPLE_NON_TECH_INTERNSHIP_RE). The codes are matched
    # case-sensitively, and "EE&P" (Employee Experience & Productivity, an
    # IS&T team) is not electrical engineering.
    noise=re.compile(
        r"\bSDETs?\b|\b(?:engineers?|developers?) in test\b|\bquality assurance\b"
        r"|(?-i:\bQ[AE]s?\b)|\bquality engineer|\btest(?:ing)? engineer"
        r"|\bsoftware quality\b|\blanguage (?:engineers?|experts?)\b"
        r"|\b(?:program|project|product) manag(?:ers?|ement)\b|(?-i:\b[ET]PMs?\b)"
        r"|(?-i:\b(?:HW)?EE\b(?!&))|(?-i:\bRF(?:HW|IC)?\b)"
        r"|\bproduct design engineer|\btooling engineering\b|\bphysical design\b"
        rf"|{_APPLE_NON_TECH_INTERNSHIP_RE}",
        re.IGNORECASE,
    ),
    # Apple files every posting under its main team (carried in
    # `Job.department`). An otherwise unmatched title in the software team
    # is DS/ML/Algos when it names a scientist ("Imaging Scientist"), and
    # SWE/DE when it names an engineer, software or an internship, the
    # non-tech internships being noise already. An engineer or software
    # title listed under the student team is SWE/DE; an engineer, research,
    # scientist or intern title in the machine-learning team is DS/ML/Algos.
    department_fallbacks=(
        _DepartmentFallback(
            "Software and Services",
            "DS/ML/Algos",
            re.compile(r"\bscientists?\b", re.IGNORECASE),
        ),
        _DepartmentFallback(
            "Software and Services",
            "SWE/DE",
            re.compile(rf"\bengineers?\b|\bsoftware\b|{_APPLE_INTERNSHIP}", re.IGNORECASE),
        ),
        _DepartmentFallback(
            "Students",
            "SWE/DE",
            re.compile(r"\bengineers?\b|\bsoftware\b", re.IGNORECASE),
        ),
        _DepartmentFallback(
            "Machine Learning and AI",
            "DS/ML/Algos",
            re.compile(
                rf"\bengineers?\b|\bresearch(?:ers?)?\b|\bscientists?\b"
                rf"|{_APPLE_INTERNSHIP}",
                re.IGNORECASE,
            ),
        ),
    ),
    # A team gives no fallback to test, quality, validation, hardware or
    # data-centre work, or to the lab, broadcast, regulatory, human-factors
    # and documentation engineers listed under the software team. A keyword
    # still routes such a title ("Senior Security Engineer, Data Center
    # Network", "Pre-Silicon ML and Compute Framework Engineer").
    fallback_veto=re.compile(
        r"\btest(?:s|ing)?\b|\bquality\b|\bvalidation\b|\bverification\b"
        r"|\bhardware\b|\bHW\b|\banalog\b|\bcircuits?\b|\b(?:pre-)?silicon\b"
        r"|\bdata[ -]?cent(?:er|re)s?\b"
        r"|\bmanufacturing\b|\bmechanical\b|\bmolding\b"
        r"|\blab\b|\bbroadcast\b|\bregulatory\b|\bhuman factors\b"
        r"|\btechnical content\b",
        re.IGNORECASE,
    ),
)

_RULES_BY_SOURCE: dict[str, _SourceRules] = {
    PROVIDER_LABELS["amazon"]: _AMAZON_RULES,
    PROVIDER_LABELS["apple"]: _APPLE_RULES,
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
        (rules.fallback_veto is None or not rules.fallback_veto.search(text))
        and not any(contains_keyword(text, keyword) for keyword in skipped)
        and not _NOT_AN_ENGINEERING_ROLE_RE.search(text)
        and not _NOT_SOFTWARE_ENGINEERING_RE.search(text)
        and not _NOT_A_SOFTWARE_ROLE_RE.search(text)
    ):
        for fallback in rules.department_fallbacks:
            if job.department == fallback.department and (
                fallback.title is None or fallback.title.search(text)
            ):
                return fallback.category
    return None
