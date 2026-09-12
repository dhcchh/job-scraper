"""Buckets a job's title (+ department) into a seniority level.

LEVELS is an ordered list of (level name, keywords), same shape and same
whole-word/whole-phrase matching as classify.py's CATEGORIES -- see that
module's docstring for why substring matching would be wrong here too (e.g.
"lead" must not match inside "Leadership").

Order is a priority list: first (i.e. most senior/most specific) match wins,
so a compound title resolves to the higher tier -- "Senior Manager" ->
Manager, "Senior Staff Engineer" -> Staff, rather than both landing on
plain Senior.

Levels roughly correspond to (title wording -> typical YOE), but that
mapping is illustrative, not something this module measures or asserts:
    Intern        UG / still in school
    Graduate      0 YOE (new-grad / campus hire, full-time)
    Junior        ~1-3 YOE
    Mid           ~3-5 YOE
    Senior        ~5-8 YOE
    Lead / Staff / Principal / Manager / Director+   -- past the point YOE
                  alone is a useful proxy; title wording is what's left.

"Unspecified" is the default for a title with none of the above -- and it's
common: across this project's real board data, ~72% of postings that pass
classify.classify_job carry no seniority wording in the title at all. This
is NOT a stand-in for "probably Mid" -- plenty of firms (trading/quant desks
especially) post one generic req and band the successful candidate's level
only after interviewing, so a title with no marker genuinely carries no
signal. Guessing "Mid" for all of those would fabricate a signal that isn't
there; leaving them clearly labeled "Unspecified" doesn't.

To extend: add a keyword to an existing tuple, or a new (name, keywords)
tuple to LEVELS -- same one-line change as classify.py.
"""

from __future__ import annotations

import re

# Order is the tie-break priority for compound titles -- most senior/most
# specific first. Intern is checked before Graduate so a hypothetical
# "Graduate Internship" (temporary) doesn't get read as a full-time
# Graduate hire.
LEVELS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Director/VP/Head",
        ("director", "vp", "vice president", "head of", "chief"),
    ),
    ("Manager", ("manager",)),
    ("Principal", ("principal", "distinguished")),
    ("Staff", ("staff",)),
    ("Lead", ("lead",)),
    ("Senior", ("senior", "sr")),
    ("Mid", ("mid level", "mid-level", "midlevel")),
    ("Junior", ("junior", "jr")),
    ("Intern", ("intern", "internship", "co-op", "working student")),
    ("Graduate", ("graduate", "new grad", "entry level", "entry-level", "trainee", "campus", "grads")),
]


def classify_seniority(title: str, department: str = "") -> str:
    """The seniority level implied by title (+ department), or "Unspecified".

    Takes raw strings rather than a Job -- this runs at Job-construction
    time (see greenhouse.client._to_job) to populate Job.seniority, before
    a Job object exists yet.
    """
    text = f"{title} {department}"
    for level, keywords in LEVELS:
        if any(_contains_keyword(text, keyword) for keyword in keywords):
            return level
    return "Unspecified"


def _contains_keyword(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None
