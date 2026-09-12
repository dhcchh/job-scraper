"""Tests for normalize_location.normalize_location -- lookup + suffix cleanup."""

import pytest

from jobscraper.classifications.normalize_location import normalize_location


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Aarhus", "Aarhus, Denmark"),
        ("Chicago", "Chicago, IL"),
        ("Chicago, IL", "Chicago, IL"),
        ("Singapore", "Singapore"),
        ("Hong Kong", "Hong Kong"),
        # A raw location.name fallback (no offices data) can be the fuller
        # "City, Region, Country" form, not just office.name's bare city.
        ("Sydney, New South Wales, Australia", "Sydney, Australia"),
    ],
)
def test_normalize_location_maps_known_names(raw, expected):
    assert normalize_location(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("New York", "New York, NY"),
        ("New York City", "New York, NY"),
        ("NYC", "New York, NY"),
        ("New York, NY", "New York, NY"),
        ("New York, NY - 120 Broadway", "New York, NY"),
    ],
)
def test_normalize_location_collapses_known_synonyms(raw, expected):
    # Same underlying city, several spellings across (or within) boards.
    assert normalize_location(raw) == expected


def test_normalize_location_strips_trailing_office_suffix_case_insensitively():
    assert normalize_location("Amsterdam office") == "Amsterdam, Netherlands"
    assert normalize_location("Chicago Office") == "Chicago, IL"


def test_normalize_location_leaves_unmapped_name_unchanged_but_stripped():
    # No fabricated city for a label that doesn't name one -- same
    # philosophy as classify_seniority's "Unspecified".
    assert normalize_location("  Australia  ") == "Australia"
    assert normalize_location("XTX Research") == "XTX Research"


def test_normalize_location_is_case_insensitive_on_lookup():
    assert normalize_location("LONDON") == "London, United Kingdom"
    assert normalize_location("london") == "London, United Kingdom"


def test_normalize_location_collapses_remote_synonyms():
    assert normalize_location("Remote") == "Remote"
    assert normalize_location("Virtual") == "Remote"
    assert normalize_location("Anywhere (subject to approval)") == "Remote"


def test_normalize_location_empty_string_stays_empty():
    assert normalize_location("") == ""
