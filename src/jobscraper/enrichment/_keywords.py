"""Word-boundary matching shared by role and seniority classifiers."""

import functools
import re


@functools.cache
def _keyword_re(keyword: str) -> re.Pattern[str]:
    # Callers pass fixed keyword lists, so the cache stays small.
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def contains_keyword(text: str, keyword: str) -> bool:
    return _keyword_re(keyword).search(text) is not None
