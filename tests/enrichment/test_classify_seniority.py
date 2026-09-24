"""Tests for seniority.classify_seniority -- keyword tiers and tie-breaks."""

import pytest

from jobscraper.enrichment.classify_seniority import classify_seniority


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


def test_classify_seniority_falls_back_to_employment_type_when_title_says_nothing():
    # The real shape this exists for: Jane Street posts its summer
    # internships under the same plain titles as its full-time reqs and
    # says "Summer Internship" only in Greenhouse's "Employment Type"
    # posting metadata (see greenhouse.client._employment_type).
    assert (
        classify_seniority("Data Engineer", "Software Engineering", "Summer Internship")
        == "Intern"
    )
    assert classify_seniority("Quantitative Trader", "", "Winter Co-Op") == "Intern"
    assert (
        classify_seniority("Software Engineer", "", "Full-Time: New Grad") == "Graduate"
    )


def test_classify_seniority_ignores_an_employment_type_with_no_level_wording():
    assert classify_seniority("Data Engineer", "", "Full-Time: Experienced") == "Unspecified"
    assert classify_seniority("Data Engineer", "", "Contractor") == "Unspecified"


def test_classify_seniority_prefers_the_title_over_the_employment_type():
    # Fallback only: a title that already carries a level keeps it, so a
    # board's employment-type field can never demote a stated level.
    assert (
        classify_seniority("Senior Data Engineer", "", "Summer Internship") == "Senior"
    )
