"""Tests for coverage -- the run summary's unplaced-location/title report."""

import datetime

from jobscraper import coverage
from jobscraper.coverage import (
    Gap,
    coverage_markdown,
    unmatched_titles,
    unresolved_locations,
)
from jobscraper.models import Job


def _job(firm, title="Software Engineer", location="Singapore"):
    return Job(firm, "1", title, "", location, "https://x", "", datetime.date(2026, 9, 27))


def test_unresolved_locations_counts_postings_and_firms_per_office():
    jobs = [
        _job("Beta", location="Knutsford"),
        _job("Acme", location="Knutsford | London, United Kingdom"),
        _job("Acme", location="Knutsford"),
        _job("Acme", location="Singapore"),
    ]

    assert unresolved_locations(jobs) == [
        Gap("Knutsford", 3, ("Acme", "Beta"), example="Software Engineer (Beta)")
    ]


def test_unresolved_locations_hints_and_leads_with_wrong_tab_suspects():
    jobs = [
        _job("Acme", location="San Francisco"),
        _job("Acme", location="San Francisco"),
        _job("Acme", location="Japan-Osaka"),
        _job("Acme", location="Mumbai, Nirlon Knowledge Park"),
    ]

    gaps = unresolved_locations(jobs)

    assert [(gap.label, gap.hint) for gap in gaps] == [
        ("Japan-Osaka", "PAC: Japan"),
        ("Mumbai, Nirlon Knowledge Park", "excluded: Mumbai, India"),
        ("San Francisco", ""),
    ]


def test_unmatched_titles_groups_by_role_phrase():
    noise = [
        _job("Acme", title="Senior Solutions Engineer"),
        _job("Beta", title="solutions engineer, EMEA"),
        _job("Acme", title="Data Science Intern"),
        _job("Acme", title="Engineering Director"),
        _job("Acme", title="Account Executive"),  # no watched word -- ignored
    ]

    gaps = unmatched_titles(noise)

    assert [(gap.label, gap.postings, gap.firms) for gap in gaps] == [
        ("Solutions Engineer", 2, ("Acme", "Beta")),
        ("Data Science", 1, ("Acme",)),
        ("Engineering", 1, ("Acme",)),
    ]
    assert gaps[0].example == "Senior Solutions Engineer (Acme)"


def test_unmatched_titles_reads_quant_and_analyst_phrases():
    noise = [
        _job("Acme", title="Quant Developer Intern"),
        _job("Acme", title="Junior Risk Analyst"),
    ]

    assert [gap.label for gap in unmatched_titles(noise)] == [
        "Quant Developer",
        "Risk Analyst",
    ]


def test_coverage_markdown_renders_both_tables():
    markdown = coverage_markdown(
        [Gap("Japan-Osaka", 2, ("Acme",), hint="PAC: Japan")],
        [Gap("Sales Engineer", 5, ("Acme", "Beta"), example="Sales Engineer (Acme)")],
    )

    assert "### Unresolved locations in new rows (1)" in markdown
    assert "| Japan-Osaka | 2 | Acme | PAC: Japan |" in markdown
    assert "### Unmatched titles (1 phrases)" in markdown
    assert "| Sales Engineer | 5 | 2 | Sales Engineer (Acme) |" in markdown


def test_coverage_markdown_says_none_when_there_are_no_gaps():
    markdown = coverage_markdown([], [])

    assert "### Unresolved locations in new rows (0)\n\nNone." in markdown
    assert "### Unmatched titles (0 phrases)\n\nNone." in markdown


def test_coverage_markdown_caps_rows_and_lists_firms_briefly(monkeypatch):
    monkeypatch.setattr(coverage, "MAX_ROWS", 1)
    locations = [
        Gap("Melbourne", 9, ("A", "B", "C", "D", "E")),
        Gap("Perth", 1, ("A",)),
    ]

    markdown = coverage_markdown(locations, [])

    assert "| Melbourne | 9 | A, B, C +2 |  |" in markdown
    assert "Perth" not in markdown
    assert "...and 1 more." in markdown


def test_coverage_markdown_escapes_pipes_in_cells():
    title = Gap("Python Engineer", 1, ("Acme",), example="Python Engineer | Perf (Acme)")

    markdown = coverage_markdown([], [title])

    assert "Python Engineer \\| Perf (Acme)" in markdown
