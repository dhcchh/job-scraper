"""Classify job seniority from title and optional employment type."""

from jobscraper.enrichment._keywords import contains_keyword

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
    (
        "Graduate",
        ("graduate", "new grad", "entry level", "entry-level", "trainee", "campus", "grads"),
    ),
]


def classify_seniority(
    title: str, department: str = "", employment_type: str = ""
) -> str:
    """Return the first seniority level matched by title or employment type."""
    level = _match(f"{title} {department}")
    if level is None and employment_type:
        level = _match(employment_type)
    return level or "Unspecified"


def _match(text: str) -> str | None:
    """Return the first matching level, if any."""
    for level, keywords in LEVELS:
        if any(contains_keyword(text, keyword) for keyword in keywords):
            return level
    return None
