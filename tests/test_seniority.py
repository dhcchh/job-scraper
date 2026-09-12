"""Tests for seniority.classify_seniority -- keyword tiers and tie-breaks."""

import pytest

from jobscraper.classifications.classify_seniority import classify_seniority


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Software Engineer Intern", "Intern"),
        ("2027 - Internship, Data Engineering", "Intern"),
        ("Graduate Software Developer", "Graduate"),
        ("Entry Level Data Analyst", "Graduate"),
        ("Junior Software Developer (C++)", "Junior"),
        ("Mid-Level Backend Engineer", "Mid"),
        ("Senior Backend Engineer", "Senior"),
        ("Sr. Data Engineer", "Senior"),
        ("Staff Software Engineer", "Staff"),
        ("Principal Engineer", "Principal"),
        ("Lead Database Engineer", "Lead"),
        ("Engineering Manager", "Manager"),
        ("Director of Engineering", "Director/VP/Head"),
        ("VP, Data Science", "Director/VP/Head"),
        ("Head of Data Engineering", "Director/VP/Head"),
        ("Software Engineer", "Unspecified"),
        ("Quantitative Researcher – HFT Futures/Equities", "Unspecified"),
    ],
)
def test_classify_seniority_routes_to_expected_level(title, expected):
    assert classify_seniority(title) == expected


def test_classify_seniority_compound_title_resolves_to_higher_tier():
    # "Senior Manager" and "Senior Staff Engineer" should resolve to the
    # more senior/specific tier, not plain Senior.
    assert classify_seniority("Senior Manager, Data Engineering") == "Manager"
    assert classify_seniority("Senior Staff Engineer") == "Staff"


def test_classify_seniority_does_not_match_substring_inside_a_longer_word():
    # "lead" inside "Leadership" must not satisfy the whole-word match.
    assert classify_seniority("Leadership Rotation Network Analyst") == "Unspecified"


def test_classify_seniority_checks_department_too():
    assert classify_seniority("Generalist", department="Internship Program") == "Intern"
