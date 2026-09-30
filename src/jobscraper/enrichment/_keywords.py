"""Word-boundary matching shared by role and seniority classifiers."""

import re


def contains_keyword(text: str, keyword: str) -> bool:
    return re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) is not None
