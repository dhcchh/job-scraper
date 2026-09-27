"""Postings the classifiers couldn't place, reported in the run summary.

Most location and keyword fixes were found by hand after rows landed in the
wrong tab; listing the gaps on every run surfaces them as they appear.
"""

import dataclasses
import re
from collections import defaultdict
from collections.abc import Iterable

from jobscraper.enrichment.classify_region import (
    classify_region,
    is_excluded,
    unresolved_offices,
)
from jobscraper.enrichment.normalize_location import normalize_location
from jobscraper.models import Job

# A noise title's role phrase around the words this project's categories are
# built from: the word before "engineer"/"analyst" ("Solutions Engineer"), the
# word after "data"/"quant" ("Data Science"). Exact titles barely repeat, so
# grouping by phrase is what makes a missing keyword stand out.
_TITLE_PHRASE_RE = re.compile(
    r"(?:\b([A-Za-z][\w.&+/]*)\s+)?\b(engineer\w*|analyst\w*)\b"
    r"|\b(data|quant\w*)\b(?:\s+([A-Za-z][\w.&+/]*))?",
    re.IGNORECASE,
)

# Pieces of an unresolved office worth re-trying on their own:
# "Japan-Osaka", "Mumbai, Nirlon Knowledge Park", "AU-ACT - Remote".
_PLACE_SEPARATOR_RE = re.compile(r"\s+-\s+|[,|/()\u2013-]")

MAX_ROWS = 25
_MAX_FIRMS_SHOWN = 3


@dataclasses.dataclass(frozen=True, slots=True)
class Gap:
    """One unplaced office or title phrase, with the postings behind it."""

    label: str
    postings: int
    firms: tuple[str, ...]
    example: str = ""
    hint: str = ""


def unresolved_locations(jobs: Iterable[Job]) -> list[Gap]:
    """Offices whose country the region rules can't read.

    An office that looks like it's really in a PAC or excluded country --
    so its rows are probably in the wrong tab -- sorts first.
    """
    jobs_by_office: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        for office in unresolved_offices(job.location):
            jobs_by_office[office].append(job)
    gaps = [
        dataclasses.replace(gap, hint=_likely_region(gap.label))
        for gap in _rank(jobs_by_office)
    ]
    return sorted(gaps, key=lambda gap: not gap.hint)


def _likely_region(office: str) -> str:
    """Name the PAC or excluded place hiding in an office, if one does."""
    parts = [part.strip() for part in _PLACE_SEPARATOR_RE.split(office)]
    words = [word for part in parts for word in part.split()]
    for candidate in dict.fromkeys(filter(None, [*parts, *words])):
        place = normalize_location(candidate)
        if classify_region(place) == "PAC":
            return f"PAC: {place}"
        if is_excluded(place):
            return f"excluded: {place}"
    return ""


def unmatched_titles(noise_jobs: Iterable[Job]) -> list[Gap]:
    """Noise title phrases mentioning engineer/data/analyst/quant."""
    jobs_by_phrase: dict[str, list[Job]] = defaultdict(list)
    first_spelling: dict[str, str] = {}
    for job in noise_jobs:
        phrase = _title_phrase(job.title)
        if phrase:
            key = first_spelling.setdefault(phrase.casefold(), phrase)
            jobs_by_phrase[key].append(job)
    return _rank(jobs_by_phrase)


def _title_phrase(title: str) -> str | None:
    match = _TITLE_PHRASE_RE.search(title)
    if match is None:
        return None
    words = match.groups()[:2] if match.group(2) else match.groups()[2:]
    return " ".join(word for word in words if word)


def _rank(jobs_by_label: dict[str, list[Job]]) -> list[Gap]:
    gaps = [
        Gap(
            label,
            len(jobs),
            tuple(sorted({job.board for job in jobs})),
            example=f"{' '.join(jobs[0].title.split())} ({jobs[0].board})",
        )
        for label, jobs in jobs_by_label.items()
    ]
    return sorted(gaps, key=lambda gap: (-gap.postings, gap.label.casefold()))


def coverage_markdown(locations: list[Gap], titles: list[Gap]) -> str:
    """Render both gap lists as run-summary sections."""
    lines = ["", f"### Unresolved locations in new rows ({len(locations)})", ""]
    if locations:
        lines += [
            "Offices of this run's newly appended rows whose country the "
            "region rules don't know, so they defaulted to RoW and skipped the "
            "excluded-country check. \"Probably\" marks the ones likely in "
            "the wrong tab. Map them in `enrichment/normalize_location.py` (or "
            "list the country in `classify_region.py`), then run a full "
            "refresh.",
            "",
            "| Office | New rows | Firms | Probably |",
            "| --- | ---: | --- | --- |",
        ]
        lines += [
            f"| {_cell(gap.label)} | {gap.postings} | {_cell(_firms(gap))} "
            f"| {_cell(gap.hint)} |"
            for gap in locations[:MAX_ROWS]
        ]
        lines += _overflow(locations)
    else:
        lines.append("None.")

    lines += ["", f"### Unmatched titles ({len(titles)} phrases)", ""]
    if titles:
        lines += [
            "Postings dropped as noise whose title mentions engineer, data, "
            "analyst or quant, grouped by role phrase -- a missing "
            "`classify_roles.py` keyword shows up here.",
            "",
            "| Phrase | Postings | Firms | Example |",
            "| --- | ---: | ---: | --- |",
        ]
        lines += [
            f"| {_cell(gap.label)} | {gap.postings} | {len(gap.firms)} "
            f"| {_cell(gap.example)} |"
            for gap in titles[:MAX_ROWS]
        ]
        lines += _overflow(titles)
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _firms(gap: Gap) -> str:
    shown = ", ".join(gap.firms[:_MAX_FIRMS_SHOWN])
    hidden = len(gap.firms) - _MAX_FIRMS_SHOWN
    return f"{shown} +{hidden}" if hidden > 0 else shown


def _overflow(gaps: list[Gap]) -> list[str]:
    hidden = len(gaps) - MAX_ROWS
    return ["", f"...and {hidden} more."] if hidden > 0 else []


def _cell(text: str) -> str:
    """Keep text from breaking out of its Markdown table cell."""
    return text.replace("|", "\\|")
