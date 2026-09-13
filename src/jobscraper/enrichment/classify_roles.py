"""Routes each fetched Job into a role category, or drops it as noise.

Every category is one tab in the Google Sheet -- see sheets.writer.get_worksheet
and main.py's per-category loop. A job matching none of the keyword lists
below (HR, Legal, Sales, Accounting, Recruiting, ...) is dropped rather than
written anywhere; that's a deliberate default, not a bug, since the point of
these categories is to keep noise out of the sheets.

To extend:
  - New keyword for an existing category (e.g. a title you're seeing that
    should route to SWE/DE but doesn't): add the string to that category's
    tuple in CATEGORIES below.
  - New category entirely: add a ("Name", (keywords...)) tuple to CATEGORIES,
    then create a tab with that exact name (and the usual FIELDS header) in
    the spreadsheet -- main.py discovers categories from this list alone.

Matching is case-insensitive whole-word/whole-phrase (regex \\b...\\b) against
the job's title ONLY, checked in CATEGORIES order with the first match
winning. Whole-word matching is deliberately strict rather than a plain
substring check -- e.g. "data engineer" must NOT match "Data Engineering
Recruiter" (the "engineer" in "engineering" isn't a word boundary) -- so
false positives (noise leaking into a category sheet) stay rare. The
trade-off is false negatives: a real posting can be missed if its exact
wording isn't in the list yet. That's the safer direction to err in here,
and it's a one-line fix (add the wording as another keyword) when you spot
one.

Title-only, not "{title} {department}", is also a false-positive guard: some
boards' department field is a broad org-unit label rather than a specific
function -- e.g. "Business Development Manager" and "Business Manager"
postings both showed up under a department literally named "Quant
Management" on one real board. Matching department too would have routed
both into QT/QR despite neither title having anything to do with trading or
research.

Categories are checked in the order below; put more specific categories
first if a future keyword could otherwise match more than one category's
list (today's lists don't overlap, so order doesn't currently change any
outcome).
"""

from __future__ import annotations

import re

from jobscraper.models import Job

# Order is a priority list, not just documentation -- first match wins, and
# three constraints drive it (see test_classify.py for all three as tests):
#   - DS/ML/Algos before SWE/DE: "algo"/"algorithm" must win over the bare
#     "developer" keyword below it, so "Algo Developer" lands on DS/ML/Algos
#     rather than SWE/DE.
#   - DA/BI before SWE/DE: "bi developer" must win over the same bare
#     "developer" keyword, so "BI Developer" lands on DA/BI rather than
#     SWE/DE.
#   - SWE/DE before QT/QR: the bare "developer" keyword must win over QT/QR's
#     bare "quant", so "Quant Developer"/"Quantitative Developer" land on
#     SWE/DE rather than QT/QR.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "DS/ML/Algos",
        (
            "algo",
            "algorithm",
            "data scientist",
            "machine learning",
            "ml",
            "applied scientist",
            "research scientist",
            "research engineer",
            "ai engineer",
            "nlp",
            "computer vision",
            "statistician",
            # Specific phrase, checked before QT/QR's bare "quantitative" for
            # the same reason "algo"/"bi developer" are checked before SWE/DE's
            # bare "developer" above: at a non-trading firm, "Quantitative
            # Analytics" names a DS/analytics function, not a trading desk --
            # confirmed against Stripe's "Treasury Finance AI and Quantitative
            # Analytics, Americas" (a Fintech posting, wrongly landing in
            # QT/QR without this). Dry-run validated against all 29 boards:
            # the only other "quant"+"analytics" titles live at actual
            # trading firms and don't use this adjacent phrasing (e.g. Qube's
            # "Quantitative Technologist - Treasury Analytics"), so they're
            # unaffected and correctly stay in QT/QR.
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
            "developer",  # broad on purpose -- covers every "X Developer" title
            "backend",
            "frontend",
            "full stack",
            "full-stack",
            "mobile engineer",
            "application engineer",
            "data engineer",
            "etl engineer",
            "big data engineer",
            "analytics engineer",
            "fpga",
            # Added when Crypto Exchange/Gaming/Fintech/big-tech boards
            # joined company_toml/greenhouse_boards.toml (dry-run validated
            # against ~3,700 real postings across 14 firms). Big-tech
            # titling favors bare "Engineer"/reordered phrases the quant-firm
            # keywords above never needed to cover:
            "staff engineer",
            "security engineer",
            "network engineer",
            "firmware engineer",
            "performance engineer",
            # Engineering *management* -- deliberately included (unlike
            # customer-facing "Solutions Engineer"/"Forward Deployed
            # Engineer"/"Support Engineer", deliberately left as noise): an
            # Engineering Manager or Director of Engineering still manages an
            # engineering org, not a sales or support one.
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
            # "quant"/"quantitative" are broad on purpose (catches e.g. a bare
            # "Pricing Quant" title, or "Quantitative Technologist") -- safe
            # only because SWE/DE (with its own "developer" keyword) is
            # checked first, so "Quant Developer" never reaches this list
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
            # Platform engineering is infrastructure/SRE work in this
            # taxonomy. Keep it only here: putting it in SWE/DE would make
            # SRE/DevOps's rule unreachable because categories are first-win.
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



# Checked before CATEGORIES, not as another entry in one of its tuples: a
# "Recruiter" title is never the role itself matching a category, no matter
# what else the title mentions -- e.g. Ripple's "Lead Technical Recruiter
# (Quant Engineering)" and Jane Street's "Campus Recruiter, Machine Learning
# and Quantitative Research" both hit a real keyword (bare "quant"/"machine
# learning") and were routing into QT/QR and DS/ML/Algos respectively despite
# being recruiting-org postings, not IC ones. Deliberately "recruiter", not
# "recruiting" -- the latter also shows up as a trailing department-style
# qualifier on a genuine IC posting (Anthropic's "People Research Scientist,
# Recruiting", a real research scientist role for the people/recruiting org)
# that vetoing on "recruiting" would wrongly drop; "recruiter" never does,
# confirmed dry-running this against all 29 boards' live postings.
_RECRUITER_RE = re.compile(r"\brecruiter\b", re.IGNORECASE)

# Same idea as the recruiter veto above: a Solutions Architect/Architecture
# title is pre-sales/customer-facing, the same family as "Solutions
# Engineer"/"Forward Deployed Engineer" already left as noise by omission --
# but unlike those, "Solutions Architect" titles were actually reaching
# SWE/DE, because several real postings name their own team in a trailing
# parenthetical or suffix that happens to contain SWE/DE's bare "developer"
# keyword: Cloudflare's "Principal Solutions Architect, APJC (Developer
# Platform) - Mandarin Speaking" and "Senior Solutions Architect, APJC
# (Developer Platform) - India Market" both matched on "developer" inside
# "(Developer Platform)", a product/org name, not the role itself. No
# keyword-list edit can fix that (the false match isn't from a bad keyword,
# it's the veto-worthy title matching an unrelated keyword by coincidence),
# so this excludes the whole title family unconditionally, matching
# "architect" not "architecture" -- the substring match still catches
# "Solutions Architecture" titles (e.g. a "Manager, Solutions Architecture"
# role) since "architect" is already a prefix of "architecture".
_SOLUTIONS_ARCHITECT_RE = re.compile(r"\bsolutions architect", re.IGNORECASE)


def classify_job(job: Job) -> str | None:
    """The category name job routes to, or None if it matches nothing (noise).

    Title only -- see the module docstring for why department is deliberately
    excluded.
    """
    text = job.title
    if _RECRUITER_RE.search(text):
        return None
    if _SOLUTIONS_ARCHITECT_RE.search(text):
        return None
    for category, keywords in CATEGORIES:
        if any(_contains_keyword(text, keyword) for keyword in keywords):
            return category
    return None


def _contains_keyword(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None
